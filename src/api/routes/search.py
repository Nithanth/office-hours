"""Search (/search) plus shuffle/reset helper routes."""

import asyncio
import html
import json as _json
import re
from datetime import datetime
from typing import Any, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

import config as _cfg
from api.deps import get_exa_client, get_llm_service, get_session_id, get_settings
from config import Config as AppConfig
from models.paper import Paper
from pages.main_page import render_search_results
from services.core import model_prefs
from services.core.exa_client import ExaClient
from services.core.exa_utils import url_utils as _url_utils
from services.limits import friendly_error_message
from services.report.report_generator import ReportGenerator
from services.tasks import task_queue
from utils.date_utils import human_date, parse_user_date
from utils.rate_limit import limiter
from utils.search_utils import clean_query

router = APIRouter()

# Set concurrency for prewarm tasks via the task queue
task_queue.set_concurrency("prewarm", _cfg.config.TEXT_PREWARM_CONCURRENCY)


# ---------------------------------------------------------------------------
# Query intent detection — one box: URL -> paper+similar, "papers by X" ->
# author search, everything else -> topic query. mode="topic" forces a plain
# query (the results banner offers this as a one-click correction).
# ---------------------------------------------------------------------------
_AUTHOR_INTENT_RE = re.compile(
    r"^(?:papers?|works?|publications?|research)\s+(?:by|from|of)\s+(.+)$",
    re.IGNORECASE,
)

# "papers by X about Y" — author + keyword triangulation in one query.
# The keyword narrows the author's publication list server-side.
_AUTHOR_TOPIC_RE = re.compile(
    r"^(?:papers?|works?|publications?|research)\s+(?:by|from|of)\s+"
    r"(?P<name>.+?)\s+(?:about|on|for)\s+(?P<keyword>.+)$",
    re.IGNORECASE,
)

# Bare person-name heuristic: 2-4 capitalized tokens (allowing lowercase
# particles like "van"/"de"/"der" mid-name). A typo'd name still matches —
# author search handles fuzziness far better than the topic pipeline, which
# would split the name and match any first OR last name.
_NAME_TOKEN = r"[A-Z][a-zA-Z''\u2019.\-]*\.?"
_NAME_PARTICLE = r"(?:van|von|de|der|den|del|di|da|le|la|bin|ibn|al|el)"
_BARE_NAME_RE = re.compile(
    rf"^{_NAME_TOKEN}(?:\s+(?:{_NAME_TOKEN}|{_NAME_PARTICLE})){{1,3}}$",
    re.UNICODE,
)


def _detect_author_query(query: str) -> tuple[str, bool, str | None] | None:
    """Return (name, explicit, keyword) for author searches — explicit means
    the query used "papers by X" phrasing; bare names are guesses that must
    be confirmed by an actual name match before routing. keyword is the
    "about Y" triangulation term, when present."""
    q = query.strip()
    m = _AUTHOR_TOPIC_RE.match(q)
    if m:
        return (
            m.group("name").strip().rstrip("?"),
            True,
            m.group("keyword").strip().rstrip("?"),
        )
    m = _AUTHOR_INTENT_RE.match(q)
    if m:
        return m.group(1).strip().rstrip("?"), True, None
    if _BARE_NAME_RE.match(q):
        return q, False, None
    return None


def _author_miss_notice(author_name: str) -> str:
    """Subtle note when a name-shaped query fell back to a topic search —
    neutral wording, since a capitalized query may be a subject ("Neural
    Scaling Laws"), not a person. Informs rather than blocks."""
    esc = html.escape(author_name)
    return (
        "<div class='intent-banner' style='padding:8px 12px;margin-bottom:10px;"
        "font-size:0.85rem;border:1px solid var(--container-border,#ccc);"
        "border-radius:6px;background:var(--container-bg,#fff);'>"
        f"<span style='opacity:0.75;'>Searched &ldquo;{esc}&rdquo; as a topic &mdash; "
        "meant the researcher? Try "
        f"&ldquo;papers by {esc}&rdquo;</span>"
        "</div>"
    )


def _intent_banner(detected: str, original_query: str) -> str:
    """Small banner naming the detected intent + a 'search as topic' escape."""
    # hx-vals must stay valid JSON after HTML-attribute decoding: build it with
    # json.dumps, then escape for the single-quoted attribute.
    esc_vals = html.escape(
        _json.dumps({"search_query": original_query, "mode": "topic"}),
        quote=True,
    )
    return (
        "<div class='intent-banner' style='display:flex;flex-wrap:wrap;gap:8px;"
        "align-items:center;padding:8px 12px;margin-bottom:10px;font-size:0.85rem;"
        "border:1px solid var(--container-border,#ccc);border-radius:6px;"
        "background:var(--container-bg,#fff);'>"
        f"<span style='opacity:0.75;'>{detected}</span>"
        "<a href='#' hx-post='/search' hx-target='#search-results-container' "
        "hx-swap='innerHTML' hx-indicator='#loading-indicator' "
        f"hx-vals='{esc_vals}' "
        "style='color:var(--oh-accent,#0d6e63);' "
        "onclick='event.preventDefault()'>Not what you meant? Search as a topic</a>"
        "</div>"
    )


async def _prewarm_cached(url: str, paper_id: str) -> None:
    """Fetch & cache paper full-text to speed up later summaries."""
    from services.paper.text_extractor import TextExtractor

    await TextExtractor.instance().ensure_cached(url, paper_id)


# Track currently running direct-query tasks per session to avoid cross-user cancellation.
_direct_query_tasks: dict[str, asyncio.Task] = {}


@limiter.limit("100/5minute")
@router.post("/search", response_class=HTMLResponse)
async def handle_search(  # noqa: PLR0913 - long signature preserves original API
    request: Request,
    search_query: str = Form(...),
    start_published_date: str | None = Form(None),
    num_results: int | None = Form(None),  # display count (1–10)
    mode: str | None = Form("auto"),  # 'auto' (detect), 'topic', 'direct'
    include_domains: list[str] | None = Form(None),  # noqa: B008
    deep_search: str | None = Form(None),  # '1' if Deep Research toggle is on
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Process the main search form (HTMX) or an Ask-LLM direct query."""

    session_id = await get_session_id(request)
    from services.core.model_prefs import prefs_owner_id
    from services.persistence.database import database_available, session_scope

    user_id = prefs_owner_id(request)

    exa_client = get_exa_client()
    llm_service = get_llm_service()

    logger.info(
        "search_request",
        query=search_query,
        start_date=start_published_date,
        num_results=num_results,
        mode=mode,
        include_domains=include_domains,
    )

    # ------------------------------------------------------------------
    # Basic config guards
    # ------------------------------------------------------------------
    if not settings.EXA_API_KEY:
        logger.error("search_missing_exa_key")
        error_html = "<div class='oh-toast'>Search service not configured. Contact support.</div>"  # minimal banner; original used render_error_message but keep dependencies slim
        return HTMLResponse(
            content=error_html,
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
                "HX-Replace-Url": "/",
            },
        )

    from services.limits import check_daily_limit

    limit_msg = await check_daily_limit(request, "search")
    if limit_msg:
        return HTMLResponse(
            f"<div class='oh-toast'>{limit_msg}</div>",
            status_code=429,
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    # Normalise display count ----------------------------------------------------
    if num_results is None:
        num_results_display = settings.DEFAULT_PAPER_COUNT
    else:
        # Clamp to 1–10
        num_results_display = max(1, min(num_results, 10))

    input_query_stripped = clean_query(search_query.strip())

    # Reject empty or excessively long queries early.
    if not input_query_stripped:
        return HTMLResponse(
            "<div class='oh-toast' style='padding:12px;border-radius:6px;"
            "background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);"
            "color:var(--oh-text,#e0e0e0);margin-top:8px;'>"
            "Please enter a search query.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    _MAX_QUERY_LEN = 500
    if len(input_query_stripped) > _MAX_QUERY_LEN:
        input_query_stripped = input_query_stripped[:_MAX_QUERY_LEN]

    # ------------------------------------------------------------------
    # Prune completed tasks to prevent unbounded dict growth
    # ------------------------------------------------------------------
    done_keys = [k for k, t in _direct_query_tasks.items() if t.done()]
    for k in done_keys:
        _direct_query_tasks.pop(k, None)

    # ------------------------------------------------------------------
    # Task-cancellation bookkeeping (per-session, not global)
    # ------------------------------------------------------------------
    prev_task = _direct_query_tasks.get(session_id)
    if prev_task and not prev_task.done():
        logger.info("cancel_previous_direct_query", session_id=session_id)
        prev_task.cancel()
        _direct_query_tasks.pop(session_id, None)

    # ------------------------------------------------------------------
    # Author-intent detection — "papers by X" / "works from X" routes into the
    # Semantic Scholar author search; a banner offers a one-click topic retry.
    # A name-shaped query that confirms no author falls through to the topic
    # pipeline with a notice, rather than silently misrouting or blocking.
    # ------------------------------------------------------------------
    author_miss_name: str | None = None
    if mode == "auto":
        detected = _detect_author_query(input_query_stripped)
        if detected:
            author_name, explicit, keyword = detected
            from api.routes.author import (
                _author_profile_html,
                _render_author_list,
            )
            from services.paper.metadata.author_search import (
                S2UnavailableError,
                all_tokens_match,
                get_author_profile,
                search_authors,
            )

            unavailable = False
            try:
                authors = await asyncio.to_thread(
                    search_authors, author_name, limit=10, timeout=10
                )
            except S2UnavailableError as exc:
                logger.warning("author_intent_unavailable", error=str(exc))
                authors = []
                unavailable = True
            except Exception as exc:  # noqa: BLE001 - fall back to topic search
                logger.warning("author_intent_failed", error=str(exc))
                authors = []
                unavailable = True
            # Bare-name guesses ("Vincent Sitzmann") only stick when a real
            # person matches every token — otherwise the query is a
            # coincidentally-capitalized topic ("Neural Scaling Laws") and
            # falls through to the normal pipeline. A confirmed miss on a
            # name-shaped query is surfaced as a notice atop the results.
            confirmed = [a for a in authors if all_tokens_match(a, author_name)]
            if not explicit and not unavailable and not confirmed:
                author_miss_name = author_name
            if explicit or confirmed:
                detected_label = (
                    f"Author search for &ldquo;{html.escape(author_name)}&rdquo;"
                )
                if keyword:
                    detected_label += (
                        f" filtered by &ldquo;{html.escape(keyword)}&rdquo;"
                    )
                banner = _intent_banner(
                    detected_label,
                    input_query_stripped,
                )
                # "papers by X about Y" with one confirmed person: land
                # straight on the filtered works — the card list is just a
                # namesake picker, and there's nothing to pick.
                pick = confirmed[0] if len(confirmed) == 1 else None
                if keyword and pick is not None:
                    try:
                        profile = await asyncio.to_thread(
                            get_author_profile,
                            pick.author_id,
                            paper_limit=30,
                            timeout=15,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("author_topic_profile_failed", error=str(exc))
                        profile = None
                    if profile is not None:
                        return HTMLResponse(
                            content=banner
                            + _author_profile_html(profile, keyword=keyword),
                            headers={
                                "HX-Retarget": "#search-results-container",
                                "HX-Reswap": "innerHTML",
                                "HX-Push-Url": f"/author/{quote(pick.author_id)}",
                            },
                        )
                return HTMLResponse(
                    content=banner
                    + _render_author_list(
                        authors, author_name, unavailable=unavailable, keyword=keyword
                    ),
                    headers={
                        "HX-Retarget": "#search-results-container",
                        "HX-Reswap": "innerHTML",
                        "HX-Push-Url": f"/authors?q={quote(author_name)}",
                    },
                )

    # ------------------------------------------------------------------
    # Direct Ask-LLM path (no Exa search)
    # ------------------------------------------------------------------
    if mode == "direct":
        this_task: asyncio.Task | None = None
        try:
            if llm_service is None or not llm_service._initialized:
                return HTMLResponse("<p>LLM service unavailable.</p>", status_code=503)

            mi = settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY]
            rg = ReportGenerator(
                llm_service=llm_service,
                exa_client=cast(ExaClient, exa_client) if exa_client else None,
                selected_model_api_id=model_prefs.resolve_model_id(
                    settings.DEFAULT_MODEL_KEY
                ),
                selected_model_provider=mi["provider"],
                selected_model_input_token_limit=mi["input_token_limit"],
            )

            async def _run() -> str:
                return cast(
                    str,
                    await rg.generate_direct_query_answer(query=input_query_stripped),
                )

            this_task = asyncio.create_task(_run())
            _direct_query_tasks[session_id] = this_task

            answer_html = await this_task
            return HTMLResponse(
                content=answer_html,
                headers={
                    "HX-Retarget": "#search-results-container",
                    "HX-Reswap": "innerHTML",
                    "HX-Push-Url": f"/answer?q={quote(input_query_stripped)}",
                },
            )
        except asyncio.CancelledError:
            return HTMLResponse("<p><i>LLM query cancelled.</i></p>")
        except Exception as exc:  # noqa: BLE001 - bubble unexpected errors
            logger.exception("direct_query_failed", error=str(exc))
            return HTMLResponse(
                f"<div class='oh-toast'>{html.escape(friendly_error_message(exc, 'Search hit a snag. Try again in a moment.'))}</div>",
                status_code=500,
            )
        finally:
            if (
                this_task
                and _direct_query_tasks.get(session_id) is this_task
                and this_task.done()
            ):
                _direct_query_tasks.pop(session_id, None)

    # ------------------------------------------------------------------
    # Standard paper search flow
    # ------------------------------------------------------------------
    try:
        if exa_client is None:
            raise RuntimeError("Exa client not initialised - check EXA_API_KEY")

        client = cast(ExaClient, exa_client)

        # Date parsing / validation ------------------------------------------------
        parsed_start_date: datetime | None = None
        if start_published_date and start_published_date.strip():
            parsed_start_date = parse_user_date(start_published_date)
            if not parsed_start_date:
                error_html = (
                    "<div class='oh-toast'>Invalid date format. Use YYYY-MM-DD.</div>"
                )
                return HTMLResponse(
                    content=error_html,
                    headers={
                        "HX-Retarget": "#search-results-container",
                        "HX-Reswap": "innerHTML",
                        "HX-Replace-Url": "/",
                    },
                )

        # Shared Exa filters -------------------------------------------------------
        shared_kwargs: dict[str, Any] = {}
        if parsed_start_date:
            shared_kwargs["start_published_date"] = parsed_start_date
        if include_domains:
            # Remove neutral options like "any" / "all" and blank items
            filtered_domains = [
                d for d in include_domains if d and d.lower() not in {"any", "all"}
            ]
            if filtered_domains:
                shared_kwargs["include_domains"] = filtered_domains
        # Deep Research mode override — uses Exa "deep" search type for
        # more comprehensive results (slower, higher quality)
        if deep_search == "1":
            shared_kwargs["type"] = "deep"

        is_url_search = _url_utils.is_paper_url(input_query_stripped)

        if is_url_search:
            exa_results = await asyncio.to_thread(
                client.url_search_and_cache,
                paper_url=input_query_stripped,
                k=num_results_display,
                session_id=session_id,
                **shared_kwargs,
            )
        else:
            search_kwargs = {"category": "publication"}
            search_kwargs.update(shared_kwargs)
            exa_results = await asyncio.to_thread(
                client.search_and_cache,
                query=input_query_stripped,
                k=num_results_display,
                session_id=session_id,
                **search_kwargs,
            )

        if user_id and database_available():
            from services.persistence.repositories import record_usage

            async for db_session in session_scope():
                await record_usage(
                    db_session,
                    user_id=user_id,
                    event_type="search",
                    provider="exa",
                    metadata={"mode": "url" if is_url_search else "query"},
                )

        # Render results ----------------------------------------------------------
        results_component = render_search_results(
            papers_list=exa_results,
            is_url_search=is_url_search,
            original_query=input_query_stripped,
            forced_topic=mode == "topic",
        )
        if is_url_search:
            results_component = _intent_banner(
                "Detected a paper link &mdash; pinned it and listed similar papers.",
                input_query_stripped,
            ) + str(results_component)
        else:
            # Lazy-load related-term chips after results render — an extra
            # LLM round-trip must not slow the paper list itself.
            from urllib.parse import quote as _quote

            notice = _author_miss_notice(author_miss_name) if author_miss_name else ""
            results_component = (
                notice
                + f"<div hx-get='/related_terms?q={_quote(input_query_stripped)}' "
                "hx-trigger='load' hx-swap='outerHTML'></div>"
            ) + str(results_component)

        # Fire-and-forget pre-warm of text cache for top 5 results -----------------
        for paper in exa_results[:5]:
            task_queue.enqueue_fn(
                "prewarm",
                _prewarm_cached,
                str(paper.url),
                paper.id,
                name=f"prewarm:{paper.id[:12]}",
                max_retries=1,
                retry_backoff=2.0,
            )

        return HTMLResponse(
            content=str(results_component),
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
                "HX-Push-Url": f"/results?q={quote(input_query_stripped)}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("search_flow_failed", error=str(exc))
        error_html = (
            "<div class='oh-toast'>Could not retrieve results. Try again later.</div>"
        )
        return HTMLResponse(
            content=error_html,
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
                "HX-Replace-Url": "/",
            },
        )


# ---------------------------------------------------------------------------
# Helper - build <li> HTML string for a Paper (avoid fasthtml for endpoints)
# ---------------------------------------------------------------------------


def _paper_li_html(
    pap: Paper, *, is_main: bool = False, wildcard: bool = False
) -> str:  # noqa: D401
    """Return minimal HTML <li> element for a paper suitable for HTMX swaps."""

    authors_display = "Author(s): N/A"
    if pap.authors:
        authors_display = f"Author(s): {html.escape(', '.join(pap.authors[:5]))}"

    formatted_date = human_date(pap.published_date) or "Unknown date"

    li_id = "wild-card-item" if wildcard else f"paper-{pap.id}"
    border = (
        "2px solid var(--main-paper-border)"
        if is_main
        else "1px solid var(--container-border, #ccc)"
    )

    # pydantic's HttpUrl objects need casting to str before escaping
    url_str = str(pap.url) if pap.url else "#"
    title_str = str(pap.title) if pap.title else "No Title"

    # Serialize full paper object for form submission (match initial render)
    paper_json = pap.model_dump_json()
    paper_json_escaped = html.escape(paper_json)

    if is_main:
        # Main (pinned) paper - render a *disabled, checked* checkbox so the
        # visual language is identical to selectable papers, but ensure it is
        # always included via a hidden input (disabled fields are not submitted).

        selection_input_html = (
            "<input type='checkbox' class='paper-select-checkbox pinned-checkbox' checked "
            "style='margin-right:5px; pointer-events:none;'>"
            f"<input type='hidden' name='selected_papers_json' value='{paper_json_escaped}'>"
            "<span class='pinned-label' style='font-size:0.85em;'>⭐</span>"
        )
    else:
        # Regular paper - user-selectable checkbox
        selection_input_html = (
            f"<input type='checkbox' class='paper-select-checkbox' style='margin-right:5px;' "
            f"name='selected_papers_json' value='{paper_json_escaped}'>"
        )

    # Qualitative chip instead of the old numeric score (rank position is
    # the relevance signal; a composite decimal read as false precision).
    signal_html = ""
    if pap.citation_count is not None and pap.citation_count >= 1000:
        signal_html = (
            f"<span class='signal-badge' title='{pap.citation_count:,} citations (Semantic Scholar)' "
            f"style='margin-left:6px;'>Highly cited</span>"
        )

    return (
        f"<li id='{html.escape(li_id)}' style='margin-bottom:1em;padding:0.5em;border:{border};position:relative;'>"
        f"<div>"
        f"{selection_input_html}"
        f"<a href='{html.escape(url_str)}' target='_blank'>{html.escape(title_str)}</a>{signal_html}<br>"
        f"<em>{authors_display}</em><br>"
        f"Published: {html.escape(formatted_date)}"
        f"</div></li>"
    )


# ---------------------------------------------------------------------------
# Shuffle / Reset helpers
# ---------------------------------------------------------------------------


@router.post("/shuffle", response_class=HTMLResponse)
async def shuffle_random_paper(
    request: Request,
    session_id: str = Depends(get_session_id),
) -> HTMLResponse:
    """Return a single random paper (wild-card) as an HTMX swap."""
    exa_client = get_exa_client()
    if exa_client is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Server starting…</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    paper = cast(ExaClient, exa_client).replace_wildcard(session_id=session_id)
    if paper is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>No more random papers available. Run a new search.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    rendered = _paper_li_html(paper, wildcard=True)
    logger.debug("shuffle_wildcard_html_snippet", snippet=rendered[:120])
    return HTMLResponse(content=rendered, headers={"HX-Reswap": "outerHTML"})


@router.post("/shuffle_all", response_class=HTMLResponse)
async def shuffle_all_papers(
    request: Request,
    session_id: str = Depends(get_session_id),
) -> HTMLResponse:
    """Shuffle the cached papers and return the refreshed `<ul>` list."""
    exa_client = get_exa_client()
    if exa_client is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    client = cast(ExaClient, exa_client)
    papers = client.shuffle_k(session_id=session_id)
    if not papers:
        return HTMLResponse(
            "<ul id='papers-list'><li>Cache cleared - run a new search before shuffling.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    cached = client.get_cached_search(session_id=session_id)
    pinned_id = cached.pinned_id if cached is not None else None

    rendered_items = [
        _paper_li_html(
            pap,
            is_main=(pinned_id is not None and pap.id == pinned_id),
            wildcard=(idx == len(papers) - 1),
        )
        for idx, pap in enumerate(papers)
    ]

    rendered_ul = (
        "<ul id='papers-list' style='list-style-type:none; padding-left:0;'>"
        + "".join(rendered_items)
        + "</ul>"
    )
    logger.debug("shuffle_all_html_snippet", snippet=rendered_ul[:120])
    return HTMLResponse(content=rendered_ul, headers={"HX-Reswap": "outerHTML"})


@router.post("/reset_results", response_class=HTMLResponse)
async def reset_papers(
    request: Request,
    session_id: str = Depends(get_session_id),
) -> HTMLResponse:
    """Reset the paper list back to the cached core search results."""
    exa_client = get_exa_client()
    if exa_client is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    client = cast(ExaClient, exa_client)
    core_list = client.reset_display(session_id=session_id)
    if not core_list:
        return HTMLResponse(
            "<ul id='papers-list'><li>Cache cleared - run a new search before resetting.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    cached = client.get_cached_search(session_id=session_id)
    pinned_id = cached.pinned_id if cached is not None else None

    rendered_items = [
        _paper_li_html(
            pap,
            is_main=(pinned_id is not None and pap.id == pinned_id),
            wildcard=(idx == len(core_list) - 1),
        )
        for idx, pap in enumerate(core_list)
    ]

    rendered_ul = (
        "<ul id='papers-list' style='list-style-type:none; padding-left:0;'>"
        + "".join(rendered_items)
        + "</ul>"
    )
    logger.debug("reset_results_html_snippet", snippet=rendered_ul[:120])
    return HTMLResponse(content=rendered_ul, headers={"HX-Reswap": "outerHTML"})


@router.get("/related_terms", response_class=HTMLResponse)
async def related_terms(
    request: Request,
    q: str,
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """LLM-suggested adjacent search terms, lazy-loaded under the results.

    Returns a chip row ("Also search:") or an empty fragment — failure is
    silent by design (the placeholder div swaps away and nothing shows).
    """
    llm_service = get_llm_service()
    if llm_service is None or not llm_service._initialized:
        return HTMLResponse("")

    prompt = (
        "A user searched an academic-paper engine for:\n"
        f'"{q.strip()[:300]}"\n\n'
        "Suggest up to 5 closely related but distinct search queries that "
        "would surface important papers this query likely misses — canonical "
        "synonyms, adjacent subfields, key techniques or named methods. "
        "Do not restate the original query. Reply with ONLY a JSON array of "
        "strings, nothing else."
    )
    try:
        mi = (
            settings.AVAILABLE_MODELS.get("fast")
            or settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY]
        )
        raw = await llm_service.generate_completion(
            provider_name=mi["provider"],
            model_name=model_prefs.resolve_model_id("fast"),
            prompt=prompt,
            max_tokens=200,
            temperature=0.4,
        )
    except Exception as exc:  # noqa: BLE001 - chips are best-effort
        logger.debug("related_terms_llm_failed", err=str(exc))
        return HTMLResponse("")

    # Lenient JSON-array extraction — models sometimes wrap in prose/fences.
    terms: list[str] = []
    m = re.search(r"\[.*?\]", raw, re.S)
    if m:
        try:
            parsed = _json.loads(m.group(0))
            terms = [str(t).strip() for t in parsed if str(t).strip()][:5]
        except Exception:  # noqa: BLE001
            pass
    if not terms:
        return HTMLResponse("")

    chips = []
    for term in terms:
        vals = html.escape(
            _json.dumps({"search_query": term, "mode": "topic"}), quote=True
        )
        chips.append(
            "<a href='#' class='oh-chip oh-related-chip' "
            "hx-post='/search' hx-target='#search-results-container' "
            "hx-swap='innerHTML' hx-indicator='#loading-indicator' "
            f"hx-vals='{vals}' onclick='event.preventDefault()'>"
            f"{html.escape(term)}</a>"
        )
    return HTMLResponse(
        "<div class='oh-related-terms'>"
        "<span class='oh-related-label'>Also search:</span>" + "".join(chips) + "</div>"
    )
