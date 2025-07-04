"""Report-related endpoints extracted from legacy `app.py`.

These routes are registered on the shared FastAPI application during the Phase-2
refactor. Behaviour remains unchanged – we have simply moved the code into an
`APIRouter` so that `app.py` can gradually be slimmed down.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client, get_llm_service, get_settings
from config import Config as AppConfig
from infra.llm_service import LLMConfigurationError
from models.paper import Paper
from pages.main_page import render_error_message, render_paper_card
from services.report_generator import ReportGenerator

# Singleton aliases (set during app startup)
exa_client_instance = get_exa_client()
llm_service_instance = get_llm_service()


router = APIRouter()


@router.post("/generate-report", response_class=HTMLResponse)
async def generate_report(  # noqa: PLR0913 – very long signature mirrors original
    request: Request,
    original_query_for_report: str = Form(...),
    selected_papers_json: list[str] = Form(...),  # noqa: B008
    audience_type: str = Form(...),
    summary_size: str = Form(...),
    selected_model_key: str = Form(...),
    is_url_input: bool = Form(False),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Generate the full HTML report (blocking).

    This is a verbatim copy of the legacy implementation in `app.py` so that the
    behaviour and responses remain unchanged while we continue the refactor.
    """

    num_papers_received = len(selected_papers_json) if selected_papers_json else 0
    log_params = {
        "original_query_for_report": original_query_for_report,
        "num_selected_papers": num_papers_received,
        "audience_type": audience_type,
        "summary_size": summary_size,
        "selected_model_key": selected_model_key,
        "is_url_input": is_url_input,
    }
    logger.info(
        "generate_report: Request received",
        endpoint="/generate-report",
        method="POST",
        params=log_params,
    )

    if llm_service_instance is None or not llm_service_instance._initialized:
        logger.critical(
            "generate_report: LLMService not initialized. Cannot generate report.",
            params=log_params,
        )
        error_html = render_error_message(
            "The report generation service is currently unavailable. Please try again later or contact support.",
            context="llm_service_unavailable",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Case-insensitive lookup for the model key
    actual_model_key_in_config: str | None = None
    for key_in_config in settings.AVAILABLE_MODELS:
        if key_in_config.lower() == selected_model_key.lower():
            actual_model_key_in_config = key_in_config
            break

    if not actual_model_key_in_config:
        logger.error(
            f"generate_report: Invalid model key '{selected_model_key}' provided.",
            params=log_params,
        )
        error_html = render_error_message(
            f"The selected model '{html.escape(selected_model_key)}' is not recognized. Please choose a valid model.",
            context="invalid_model_key",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    selected_model_details = settings.AVAILABLE_MODELS[actual_model_key_in_config]
    selected_model_api_id = selected_model_details["id"]
    selected_model_provider = selected_model_details["provider"]
    selected_model_input_token_limit = selected_model_details["input_token_limit"]

    try:
        deserialized_papers = [Paper.model_validate_json(p_json) for p_json in selected_papers_json]
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "generate_report: Error deserializing paper JSON",
            error=str(exc),
            num_papers_json=num_papers_received,
            params=log_params,
        )
        error_html = render_error_message(
            "Could not process paper data. Please try selecting papers again.",
            context="paper_deserialization_error",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    if not deserialized_papers:
        logger.warning(
            "generate_report: No papers provided for report generation after deserialization",
            params=log_params,
        )
        error_html = render_error_message(
            "No papers were selected to generate a report.", context="no_papers_for_report"
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Ensure Exa client is available (initialized in lifespan)
    global exa_client_instance  # noqa: PLW0603 – steers clear of circulars for now
    if exa_client_instance is None:
        logger.critical(
            "generate_report: Exa Service not available. Cannot generate report.", params=log_params
        )
        error_html = render_error_message(
            "The paper information service is currently unavailable. Please try again later or contact support.",
            context="exa_service_unavailable",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # ------------------------------------------------------------------
    # Build the paper lists depending on whether the input was a URL or
    # a topic-style search.
    # ------------------------------------------------------------------
    main_paper_for_report: Paper | None = None
    similar_papers_for_report: list[Paper] = []
    topic_papers_for_report: list[Paper] = []

    if is_url_input:
        for paper in deserialized_papers:
            if paper.url == original_query_for_report:
                main_paper_for_report = paper
            else:
                similar_papers_for_report.append(paper)
        logger.info(
            "generate_report: URL input – main paper identified",
            main_paper_title=main_paper_for_report.title if main_paper_for_report else "<none>",
            num_similar=len(similar_papers_for_report),
        )
    else:
        topic_papers_for_report = deserialized_papers
        logger.info(
            "generate_report: Topic input – papers for report",
            num_papers=len(topic_papers_for_report),
        )

    # Guard rails – ensure we have something to summarise
    if is_url_input and not (main_paper_for_report or similar_papers_for_report):
        error_html = render_error_message(
            "No papers were selected from the URL search results to generate a report.",
            context="no_url_papers_for_report",
        ).render()
        return HTMLResponse(content=error_html)
    if (not is_url_input) and not topic_papers_for_report:
        error_html = render_error_message(
            "No papers were selected to generate a report.", context="no_topic_papers_for_report"
        ).render()
        return HTMLResponse(content=error_html)

    # ------------------------------------------------------------------
    # Generate the report via the service layer
    # ------------------------------------------------------------------
    try:
        report_generator = ReportGenerator(
            llm_service=llm_service_instance,
            exa_client=exa_client_instance,
            selected_model_api_id=selected_model_api_id,
            selected_model_provider=selected_model_provider,
            selected_model_input_token_limit=selected_model_input_token_limit,
        )

        report_html = await report_generator.generate_report_html(
            query_or_url=original_query_for_report,
            papers_to_summarize=deserialized_papers,
            audience_type=audience_type,
            summary_size=summary_size,
            is_url_input=is_url_input,
            main_input_paper=main_paper_for_report,
            selected_similar_papers_data=similar_papers_for_report,
            papers_for_topic_report=topic_papers_for_report,
        )
        return HTMLResponse(content=report_html)
    except LLMConfigurationError as llm_err:
        logger.error(
            "generate_report: LLM config error",
            error=str(llm_err),
            params=log_params,
        )
        error_html = render_error_message(
            f"LLM Configuration Error: {llm_err}. Please check API keys and model settings.",
            context="llm_config_error_report_gen",
        ).render()
        return HTMLResponse(content=error_html)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "generate_report: Unexpected error during report generation",
            error=str(exc),
            params=log_params,
        )
        error_html = render_error_message(
            f"An unexpected error occurred while generating the report: {exc}",
            context="unexpected_report_gen_error",
        ).render()
        return HTMLResponse(content=error_html)


@router.get("/papers/{paper_id}/card")
async def citation_card(paper_id: str) -> Response:
    """Return the small hover-card HTML for a citation link."""

    # TODO: When we have a paper-lookup service, attempt to fetch real details
    paper = None  # placeholder – minimise behavioural change

    if paper is None:
        return HTMLResponse(
            content=f"<div style='padding:6px;font-size:0.8em;'>Paper {paper_id}</div>"
        )

    return HTMLResponse(content=str(render_paper_card(paper)))
