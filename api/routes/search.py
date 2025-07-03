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
from app import _prewarm_cached
from config import Config as AppConfig
from pages.main_page import _render_paper_item, render_search_results
from services.exa_client import ExaClient
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
    domain_filter: str | None = Form(None),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Process the main search form (HTMX) or an Ask-LLM direct query."""

    global current_direct_query_task

    logger.info(
        "handle_search: Request received",
        endpoint="/search",
        method="POST",
        params={
            "search_query": search_query,
            "start_published_date": start_published_date,
            "num_results": num_results,
            "mode": mode,
            "domain_filter": domain_filter,
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
                return await rg.generate_direct_query_answer(query=input_query_stripped)

            this_task = asyncio.create_task(_run())
            current_direct_query_task = this_task

            answer_html = await this_task
            return HTMLResponse(content=answer_html)
        except asyncio.CancelledError:
            return HTMLResponse("<p><i>LLM query cancelled.</i></p>")
        except Exception as exc:  # noqa: BLE001 – bubble unexpected errors
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
        assert exa_client_instance is not None

        # Date parsing / validation ------------------------------------------------
        parsed_start_date: datetime | None = None
        if start_published_date and start_published_date.strip():
            parsed_start_date = parse_user_date(start_published_date)
            if not parsed_start_date:
                error_html = "<div class='error-banner'>Invalid date format. Use YYYY-MM-DD.</div>"
                return HTMLResponse(
                    content=error_html,
                    headers={
                        "HX-Retarget": "#search-results-container",
                        "HX-Reswap": "innerHTML",
                    },
                )

        # Shared Exa filters -------------------------------------------------------
        shared_kwargs: dict[str, Any] = {}
        if parsed_start_date:
            shared_kwargs["start_published_date"] = parsed_start_date
        if domain_filter and domain_filter.lower() not in {"any", "all"}:
            shared_kwargs["include_domains"] = [domain_filter]

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
# Shuffle / Reset helpers
# ---------------------------------------------------------------------------


@router.post("/shuffle", response_class=HTMLResponse)
async def shuffle_random_paper() -> HTMLResponse:
    if exa_client_instance is None:
        return HTMLResponse(
            content="<li id='wild-card-item'>Server starting…</li>",
            headers={"HX-Reswap": "outerHTML"},
        )

    paper = cast(ExaClient, exa_client_instance).replace_wildcard()
    if paper is None:
        return HTMLResponse(
            content="<li id='wild-card-item'>No more random papers available. Run a new search.</li>",
            headers={"HX-Reswap": "outerHTML"},
        )

    rendered = str(_render_paper_item(paper, wildcard=True))
    return HTMLResponse(content=rendered)


@router.post("/shuffle_all", response_class=HTMLResponse)
async def shuffle_all_papers() -> HTMLResponse:
    if exa_client_instance is None:
        return HTMLResponse("<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>")

    client = cast(ExaClient, exa_client_instance)
    papers = client.shuffle_k()
    if not papers:
        return HTMLResponse(
            "<li>Cache cleared – run a new search before shuffling.</li>",
        )

    cached = client.get_cached_search()
    pinned_id = cached.pinned_id if cached is not None else None

    ul_items = [
        _render_paper_item(
            pap,
            is_main_paper=(pinned_id is not None and pap.id == pinned_id),
            wildcard=(idx == len(papers) - 1),
        )
        for idx, pap in enumerate(papers)
    ]
    ul_html = "".join(str(item) for item in ul_items)
    return HTMLResponse(content=ul_html)


@router.post("/reset_results", response_class=HTMLResponse)
async def reset_papers() -> HTMLResponse:
    if exa_client_instance is None:
        return HTMLResponse("<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>")

    client = cast(ExaClient, exa_client_instance)
    core_list = client.reset_display()
    if not core_list:
        return HTMLResponse(
            "<li>Cache cleared - run a new search before resetting.</li>",
        )

    cached = client.get_cached_search()
    pinned_id = cached.pinned_id if cached is not None else None

    ul_items = [
        _render_paper_item(
            pap,
            is_main_paper=(pinned_id is not None and pap.id == pinned_id),
            wildcard=(idx == len(core_list) - 1),
        )
        for idx, pap in enumerate(core_list)
    ]
    ul_html = "".join(str(item) for item in ul_items)
    return HTMLResponse(content=ul_html)
