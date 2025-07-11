"""Search and result-management endpoints extracted from legacy `app.py`.

This module handles:
• POST /search               - main Exa search and direct LLM query flow
• POST /shuffle              - replace the wild-card paper
• POST /shuffle_all          - reshuffle current result set
• POST /reset_results        - revert to original ranking

Behaviour is byte-for-byte equivalent to the original code; only
organisation has changed.
"""

from __future__ import annotations

import asyncio
import html
from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client, get_llm_service, get_settings
from config import Config as AppConfig
from infra.exa_client import ExaClient
from main import _prewarm_cached
from models.paper import Paper
from pages.main_page import render_search_results
from services.report_generator import ReportGenerator
from utils.date_utils import parse_user_date
from utils.search_utils import clean_query

router = APIRouter()

# Singleton aliases (populated during app startup)
exa_client_instance = get_exa_client()
llm_service_instance = get_llm_service()

# Track the currently running direct-query task so that a new request can cancel it.
current_direct_query_task: asyncio.Task | None = None


@router.post("/search", response_class=HTMLResponse)
async def handle_search(  # noqa: PLR0913 - long signature preserves original API
    request: Request,
    search_query: str = Form(...),
    start_published_date: str | None = Form(None),
    num_results: int | None = Form(None),  # default resolved via settings below
    mode: str | None = Form("auto"),  # 'auto' (default), 'direct'
    include_domains: list[str] | None = Form(None),  # noqa: B008
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Process the main search form (HTMX) or an Ask-LLM direct query."""

    global current_direct_query_task, exa_client_instance, llm_service_instance

    # Refresh singleton handles - route modules capture them at *import* time,
    # but Uvicorn reload / multiprocessing means the objects may be assigned
    # later during the app lifespan event.  Lazily fetch again if still None.
    if exa_client_instance is None:
        exa_client_instance = get_exa_client()
    if llm_service_instance is None:
        llm_service_instance = get_llm_service()

    logger.info(
        "handle_search: Request received",
        endpoint="/search",
        method="POST",
        params={
            "query": search_query,
            "start_published_date": start_published_date,
            "num_results": num_results,
            "mode": mode,
            "include_domains": include_domains,
        },
    )

    # ------------------------------------------------------------------
    # Basic config guards
    # ------------------------------------------------------------------
    if not settings.EXA_API_KEY:
        logger.error("handle_search: Exa API key not configured", endpoint="/search")
        error_html = (  # minimal banner; original used render_error_message but keep dependencies slim
            "<div class='error-banner'>Search service not configured. Contact support.</div>"
        )
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"},
        )

    input_query_stripped = clean_query(search_query.strip())

    # ------------------------------------------------------------------
    # Task-cancellation bookkeeping
    # ------------------------------------------------------------------
    if mode != "direct":  # a paper search → cancel any in-flight Ask-LLM task
        if current_direct_query_task and not current_direct_query_task.done():
            logger.info("Paper search initiated - cancelling active direct-query task.")
            current_direct_query_task.cancel()
            current_direct_query_task = None
    else:  # new direct Ask-LLM query → cancel earlier one
        if current_direct_query_task and not current_direct_query_task.done():
            logger.info("New direct query - cancelling previous task.")
            current_direct_query_task.cancel()
            current_direct_query_task = None

    # ------------------------------------------------------------------
    # Direct Ask-LLM path (no Exa search)
    # ------------------------------------------------------------------
    if mode == "direct":
        this_task: asyncio.Task | None = None
        try:
            if llm_service_instance is None or not llm_service_instance._initialized:
                return HTMLResponse("<p>LLM service unavailable.</p>", status_code=503)

            mi = settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY]
            rg = ReportGenerator(
                llm_service=llm_service_instance,
                exa_client=cast(ExaClient, exa_client_instance),
                selected_model_api_id=mi["id"],
                selected_model_provider=mi["provider"],
                selected_model_input_token_limit=mi["input_token_limit"],
            )

            async def _run() -> str:
                return cast(str, await rg.generate_direct_query_answer(query=input_query_stripped))

            this_task = asyncio.create_task(_run())
            current_direct_query_task = this_task

            answer_html = await this_task
            return HTMLResponse(content=answer_html)
        except asyncio.CancelledError:
            return HTMLResponse("<p><i>LLM query cancelled.</i></p>")
        except Exception as exc:  # noqa: BLE001 - bubble unexpected errors
            logger.exception("Direct query failed", err=str(exc))
            return HTMLResponse(
                f"<div class='error-banner'>Error: {html.escape(str(exc))}</div>",
                status_code=500,
            )
        finally:
            if this_task and current_direct_query_task is this_task and this_task.done():
                current_direct_query_task = None

    # ------------------------------------------------------------------
    # Standard paper search flow
    # ------------------------------------------------------------------
    try:
        assert exa_client_instance is not None, "Exa client not initialised - check EXA_API_KEY"

        # Date parsing / validation ------------------------------------------------
        parsed_start_date: datetime | None = None
        if start_published_date and start_published_date.strip():
            parsed_start_date = parse_user_date(start_published_date)
            if not parsed_start_date:
                error_html = "<div class='error-banner'>Invalid date format. Use YYYY-MM-DD.</div>"
                return HTMLResponse(
                    content=error_html,
                    headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"},
                )

        # Shared Exa filters -------------------------------------------------------
        shared_kwargs: dict[str, Any] = {}
        if parsed_start_date:
            shared_kwargs["start_published_date"] = parsed_start_date
        if include_domains:
            # Remove neutral options like "any" / "all" and blank items
            filtered_domains = [d for d in include_domains if d and d.lower() not in {"any", "all"}]
            if filtered_domains:
                shared_kwargs["include_domains"] = filtered_domains

        client = cast(ExaClient, exa_client_instance)
        is_url_search = client.is_url(input_query_stripped)

        if is_url_search:
            exa_results = client.url_search_and_cache(
                paper_url=input_query_stripped,
                k=num_results or settings.DEFAULT_PAPER_COUNT,
                **shared_kwargs,
            )
        else:
            search_kwargs = {"use_autoprompt": True, "category": "research_paper"}
            search_kwargs.update(shared_kwargs)
            exa_results = client.search_and_cache(
                query=input_query_stripped,
                k=num_results or settings.DEFAULT_PAPER_COUNT,
                **search_kwargs,
            )

        # Render results ----------------------------------------------------------
        results_component = render_search_results(
            papers_list=exa_results,
            is_url_search=is_url_search,
            original_query=input_query_stripped,
        )

        # Fire-and-forget pre-warm of text cache for top 5 results -----------------
        for paper in exa_results[:5]:
            asyncio.create_task(_prewarm_cached(str(paper.url), paper.id))

        return HTMLResponse(content=str(results_component))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Search flow failed", err=str(exc))
        error_html = "<div class='error-banner'>Could not retrieve results. Try again later.</div>"
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"},
        )


# ---------------------------------------------------------------------------
# Helper - build <li> HTML string for a Paper (avoid fasthtml for endpoints)
# ---------------------------------------------------------------------------


def _paper_li_html(pap: Paper, *, is_main: bool = False, wildcard: bool = False) -> str:  # noqa: D401
    """Return minimal HTML <li> element for a paper suitable for HTMX swaps."""

    authors_display = "Author(s): N/A"
    if pap.authors:
        authors_display = f"Author(s): {html.escape(', '.join(pap.authors[:5]))}"

    formatted_date = (
        pap.published_date.strftime("%B %d, %Y")
        if isinstance(pap.published_date, datetime)
        else "Unknown date"
    )

    li_id = "wild-card-item" if wildcard else f"paper-{pap.id}"
    border = "2px solid var(--main-paper-border)" if is_main else "1px solid #ccc"

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
            "<span class='pinned-label' style='font-size:0.85em;'>📌 pinned</span>"
        )
    else:
        # Regular paper - user-selectable checkbox
        selection_input_html = (
            f"<input type='checkbox' class='paper-select-checkbox' style='margin-right:5px;' "
            f"name='selected_papers_json' value='{paper_json_escaped}'>"
        )

    return (
        f"<li id='{html.escape(li_id)}' style='margin-bottom:1em;padding:0.5em;border:{border};position:relative;'>"
        f"<div>"
        f"{selection_input_html}"
        f"<a href='{html.escape(url_str)}' target='_blank'>{html.escape(title_str)}</a><br>"
        f"<em>{authors_display}</em><br>"
        f"Published: {html.escape(formatted_date)}"
        f"</div></li>"
    )


# ---------------------------------------------------------------------------
# Shuffle / Reset helpers
# ---------------------------------------------------------------------------


@router.post("/shuffle", response_class=HTMLResponse)
async def shuffle_random_paper() -> HTMLResponse:
    global exa_client_instance

    if exa_client_instance is None:
        exa_client_instance = get_exa_client()
    if exa_client_instance is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Server starting…</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    paper = cast(ExaClient, exa_client_instance).replace_wildcard()
    if paper is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>No more random papers available. Run a new search.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    rendered = _paper_li_html(paper, wildcard=True)
    logger.info("shuffle (wildcard) HTML → {}...", rendered[:200])
    return HTMLResponse(content=rendered, headers={"HX-Reswap": "outerHTML"})


@router.post("/shuffle_all", response_class=HTMLResponse)
async def shuffle_all_papers() -> HTMLResponse:
    global exa_client_instance

    if exa_client_instance is None:
        exa_client_instance = get_exa_client()
    if exa_client_instance is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    client = cast(ExaClient, exa_client_instance)
    papers = client.shuffle_k()
    if not papers:
        return HTMLResponse(
            "<ul id='papers-list'><li>Cache cleared - run a new search before shuffling.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    cached = client.get_cached_search()
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
    logger.info("shuffle_all HTML → {}...", rendered_ul[:200])
    return HTMLResponse(content=rendered_ul, headers={"HX-Reswap": "outerHTML"})


@router.post("/reset_results", response_class=HTMLResponse)
async def reset_papers() -> HTMLResponse:
    global exa_client_instance

    if exa_client_instance is None:
        exa_client_instance = get_exa_client()
    if exa_client_instance is None:
        return HTMLResponse(
            "<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    client = cast(ExaClient, exa_client_instance)
    core_list = client.reset_display()
    if not core_list:
        return HTMLResponse(
            "<ul id='papers-list'><li>Cache cleared - run a new search before resetting.</li></ul>",
            headers={"HX-Reswap": "outerHTML"},
        )

    cached = client.get_cached_search()
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
    logger.info("reset_results HTML → {}...", rendered_ul[:200])
    return HTMLResponse(content=rendered_ul, headers={"HX-Reswap": "outerHTML"})
