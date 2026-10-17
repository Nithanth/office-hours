"""Home page (`/`) and report-configuration (`/show-report-config`) routes."""

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client, get_settings
from config import Config as AppConfig
from models.paper import Paper
from pages.main_page import (
    SharedPageData,
    home_page,
    render_error_message,
    render_report_config_form,
    welcome_page,
)
from services.auth.session import get_auth_user_id
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.repositories import list_topics

# No module-level singleton to avoid stale DI instances.

router = APIRouter()


# Routes
@router.get("/", response_class=HTMLResponse)
async def get_home(
    request: Request,
    search_query: str | None = None,
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Render the main home page and optionally perform an initial search."""

    logger.info("home_request", query=search_query)
    page_data = SharedPageData(current_query=search_query)

    # When login is required and the visitor isn't signed in, swap the home
    # page for a dedicated welcome / sign-in card. Without this, an
    # unauthenticated user sees the full home UI (search bar, etc.) but every
    # action they take 302s to Google, which is confusing.
    if settings.REQUIRE_LOGIN and not get_auth_user_id(request):
        return HTMLResponse(f"<!DOCTYPE html>{str(welcome_page(page_data=page_data))}")

    # Always clear any previous in-memory search cache so hard refresh starts clean.
    try:
        exa_client = get_exa_client()
        if exa_client:
            exa_client.reset_cached_search()
    except Exception:
        # Safe best-effort – avoid failing page render if DI unavailable
        pass

    # Perform initial search if query is provided
    if search_query and search_query.strip():
        if not settings.EXA_API_KEY:
            logger.error("home_missing_exa_key")
        else:
            exa_client = get_exa_client()
            if exa_client is None:
                logger.error("home_exa_client_unavailable")
            else:
                try:
                    logger.info(
                        "home_initial_search",
                        query=search_query.strip(),
                        k=settings.DEFAULT_PAPER_COUNT,
                    )
                    papers = await asyncio.to_thread(
                        exa_client.search_and_cache,
                        query=search_query.strip(),
                        k=settings.DEFAULT_PAPER_COUNT,
                    )
                    page_data.search_results = papers
                except Exception as exc:  # log and fall back
                    logger.exception(
                        "home_initial_search_error",
                        query=search_query.strip(),
                        error=str(exc),
                    )
                    page_data.search_results = []

    # Recent topics for the "Your topics" strip + research-surface counts
    if database_available():
        try:
            owner = prefs_owner_id(request)
            if owner:
                from services.persistence.repositories import (
                    feed_new_counts,
                    home_stats,
                )

                async for session in session_scope():
                    page_data.recent_topics = await list_topics(
                        session, owner_id=owner, limit=6
                    )
                    page_data.home_stats = await home_stats(session, owner_id=owner)
                    page_data.topic_new_counts = await feed_new_counts(
                        session,
                        topic_ids=[t.id for t in page_data.recent_topics],
                    )
        except Exception as exc:  # noqa: BLE001 - strip is best-effort
            logger.warning("home_topics_strip_failed", error=str(exc))

    # Prepend a standards-mode doctype to avoid browser Quirks Mode.
    return HTMLResponse(f"<!DOCTYPE html>{str(home_page(page_data=page_data))}")


# In-process TTL cache for the for-you row — recommendation calls (Exa
# find_similar) shouldn't fire on every home render.
_foryou_cache: dict[str, tuple[float, dict]] = {}


@router.get("/api/home/for-you")
async def home_for_you(request: Request) -> HTMLResponse:
    """Lazy-loaded 'for you' recommendations: similar papers to recent saves
    + authors that recur across the library. Rendered as chips."""
    import time
    from html import escape as _esc

    owner = prefs_owner_id(request)
    empty = HTMLResponse("<span class='oh-foryou-empty'></span>")
    if not owner or not database_available():
        return empty

    cached = _foryou_cache.get(owner)
    if cached and time.monotonic() - cached[0] < 3600:
        return HTMLResponse(cached[1]["html"])

    from services.persistence.repositories import (
        list_saved_paper_ids,
        list_saved_papers,
    )

    try:
        async for session in session_scope():
            saved = await list_saved_papers(session, owner_id=owner)
            saved_ids = await list_saved_paper_ids(session, owner_id=owner)

        # Authors: rank by how many saved papers carry them — recurring
        # names are the people whose work keeps resonating.
        counts: dict[str, int] = {}
        for p in saved:
            names = (p.authors or {}).get("list") or []
            for n in names:
                n = n.strip()
                if n:
                    counts[n] = counts.get(n, 0) + 1
        authors_html: list[str] = []
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
            if n < 2:
                continue  # one-off authors aren't a signal
            authors_html.append(
                f'<a class="oh-foryou-chip" href="/?search_query={_esc(name)}"'
                f' title="Author on {n} of your saved papers — click to search">'
                f"{_esc(name)} ×{n}</a>"
            )

        # Papers: Exa find_similar seeded by the 2 most recent saves w/ URLs.
        papers_html: list[str] = []
        exa = get_exa_client()
        if exa is not None:
            for seed in [p for p in saved if p.url][:2]:
                try:
                    sims = await asyncio.to_thread(exa.find_similar_papers, seed.url, 5)
                except Exception:
                    continue
                for pap in sims:
                    pap_url = str(pap.url) if pap.url else ""
                    pap_id = str(pap.id) if pap.id else ""
                    if not pap_url or pap_url in saved_ids or pap_id in saved_ids:
                        continue
                    title = (str(pap.title) if pap.title else pap_url).strip()
                    if not title:
                        continue
                    papers_html.append(
                        '<a class="oh-foryou-chip oh-foryou-paper"'
                        f' href="{_esc(pap_url)}" target="_blank" rel="noopener"'
                        ' title="Similar to one of your saves">'
                        f"{_esc(title[:90])}</a>"
                    )
                    if len(papers_html) >= 4:
                        break
                if len(papers_html) >= 4:
                    break

        body = ""
        if authors_html:
            body += '<span class="oh-foryou-label">Authors to watch</span> ' + " ".join(
                authors_html
            )
        if papers_html:
            body += (
                ' <span class="oh-foryou-label">Papers you may like</span> '
                + " ".join(papers_html)
            )
        html = f'<div class="oh-foryou-inner">{body}</div>' if body else ""
    except Exception:
        # Recommendations are best-effort: fail silent, never paint an error.
        logger.warning("home_for_you_failed", owner=owner)
        return empty

    _foryou_cache[owner] = (time.monotonic(), {"html": html})
    return HTMLResponse(html)


@router.post("/show-report-config", response_class=HTMLResponse)
async def show_report_config(
    request: Request,
    original_search_query_for_report: str = Form(...),  # noqa: B008
    selected_papers_json: list[str] | None = Form(None),  # noqa: B008
    is_url_input_source: str | None = Form(None),  # noqa: B008
) -> HTMLResponse:
    """Display the report-configuration form for the selected papers."""

    is_url = is_url_input_source == "true"
    num_papers_received = len(selected_papers_json) if selected_papers_json else 0

    # No need to retain raw form data – remove unused variable.
    logger.info(
        "report_config_request",
        original_query=original_search_query_for_report,
        papers_received=num_papers_received,
        is_url_input_source=is_url_input_source,
    )

    if not selected_papers_json:
        config_form_component = render_report_config_form(
            original_query=original_search_query_for_report,
            selected_papers_json_list=[],
            num_selected=0,
            is_url_input=is_url,
        )
        return HTMLResponse(str(config_form_component))

    # Deserialize papers
    deserialized_papers: list[Paper] = []
    try:
        for paper_json_str in selected_papers_json:
            deserialized_papers.append(Paper.model_validate_json(paper_json_str))
    except Exception as exc:  # deserialization failed
        logger.exception("report_config_deserialize_error", error=str(exc))
        error_html = render_error_message(
            "Could not process the selected paper data. Please try again.",
            context="paper_deserialization_error_show_config",
        ).render()
        return HTMLResponse(
            error_html,
            headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"},
        )

    if not deserialized_papers:
        error_html = render_error_message(
            "No valid paper data was processed. Please select papers again.",
            context="no_valid_papers_processed",
        ).render()
        return HTMLResponse(
            error_html,
            headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"},
        )

    logger.info(
        "report_config_render",
        query=original_search_query_for_report,
        num_papers=len(deserialized_papers),
    )

    config_form_component = render_report_config_form(
        original_query=original_search_query_for_report,
        selected_papers_json_list=selected_papers_json,
        num_selected=len(deserialized_papers),
        is_url_input=is_url,
    )
    return HTMLResponse(str(config_form_component))
