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
)

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
                        "home_initial_search_error", query=search_query.strip(), error=str(exc)
                    )
                    page_data.search_results = []

    # Prepend a standards-mode doctype to avoid browser Quirks Mode.
    return HTMLResponse(f"<!DOCTYPE html>{str(home_page(page_data=page_data))}")


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
            error_html, headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"}
        )

    if not deserialized_papers:
        error_html = render_error_message(
            "No valid paper data was processed. Please select papers again.",
            context="no_valid_papers_processed",
        ).render()
        return HTMLResponse(
            error_html, headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"}
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
