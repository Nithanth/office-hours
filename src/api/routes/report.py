"""Report generation (`/generate-report`) and citation card routes."""

import html

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client, get_llm_service, get_settings
from config import Config as AppConfig
from models.paper import Paper
from pages.main_page import render_error_message, render_paper_card
from services.core import model_prefs
from services.core.exa_client import ExaClient
from services.core.exa_utils import url_utils as _url_utils
from services.core.llm_service import LLMConfigurationError, LLMService
from services.report.report_generator import ReportGenerator
from utils.rate_limit import limiter

# Services are obtained per-request via dependency helpers to avoid stale instances.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_url(text: str) -> bool:
    """Return True if *text* resembles an HTTP(S) URL."""

    try:
        from urllib.parse import urlparse

        parsed = urlparse(text.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


router = APIRouter()


@limiter.limit("20/5minute")
@router.post("/generate-report", response_class=HTMLResponse)
async def generate_report(  # noqa: PLR0913 - very long signature mirrors original
    request: Request,
    original_query_for_report: str = Form(...),
    selected_papers_json: list[str] = Form(...),  # noqa: B008
    audience_type: str = Form(...),
    summary_size: str = Form(...),
    selected_model_key: str = Form(...),
    is_url_input: bool = Form(False),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
    llm_service: LLMService | None = Depends(get_llm_service),  # noqa: B008
    exa_client: ExaClient | None = Depends(get_exa_client),  # noqa: B008
) -> HTMLResponse:
    """Generate the full HTML report (blocking).

    Behaviour mirrors the legacy implementation while refactor is underway.
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
    # Auto-detect URL inputs to avoid relying solely on the form checkbox.
    if not is_url_input and _looks_like_url(original_query_for_report):
        is_url_input = True
        log_params["is_url_input"] = True

    logger.info("report_request", **log_params)

    if llm_service is None or not llm_service._initialized:
        logger.critical("report_llm_unavailable", **log_params)
        error_html = render_error_message(
            "The report generation service is currently unavailable. Please try again later or contact support.",
            context="llm_service_unavailable",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Case-insensitive lookup for the model key
    actual_model_key_in_config = settings.resolve_model_key(selected_model_key)

    if not actual_model_key_in_config:
        logger.error(
            "report_invalid_model_key", model_key=selected_model_key, **log_params
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
    selected_model_api_id = model_prefs.resolve_model_id(actual_model_key_in_config)
    selected_model_provider = selected_model_details["provider"]
    selected_model_input_token_limit = selected_model_details["input_token_limit"]

    try:
        deserialized_papers = [
            Paper.model_validate_json(p_json) for p_json in selected_papers_json
        ]
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "report_deserialize_error",
            error=str(exc),
            papers_json=num_papers_received,
            **log_params,
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
        logger.warning("report_no_papers_after_deserialize", **log_params)
        error_html = render_error_message(
            "No papers were selected to generate a report.",
            context="no_papers_for_report",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Ensure Exa client is available
    if exa_client is None:
        logger.critical("report_exa_unavailable", **log_params)
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
        similar_candidates: list[Paper] = []
        for paper in deserialized_papers:
            url_str = str(paper.url)
            if main_paper_for_report is None and _url_utils.url_equivalent(
                url_str, original_query_for_report
            ):
                main_paper_for_report = paper
            else:
                similar_candidates.append(paper)

        if main_paper_for_report is None and deserialized_papers:
            # Fall back to the first selected paper when canonical URLs differ
            main_paper_for_report = deserialized_papers[0]
            similar_candidates = deserialized_papers[1:]

        if main_paper_for_report is not None:
            main_id = main_paper_for_report.id
            similar_papers_for_report = [
                p for p in similar_candidates if p.id != main_id
            ]
        else:
            similar_papers_for_report = similar_candidates
        logger.info(
            "report_url_mode",
            main_paper_title=(
                main_paper_for_report.title if main_paper_for_report else "<none>"
            ),
            num_similar=len(similar_papers_for_report),
        )
    else:
        topic_papers_for_report = deserialized_papers
        logger.info("report_topic_mode", num_papers=len(topic_papers_for_report))

    # Guard rails - ensure we have something to summarise
    if is_url_input and not (main_paper_for_report or similar_papers_for_report):
        error_html = render_error_message(
            "No papers were selected from the URL search results to generate a report.",
            context="no_url_papers_for_report",
        ).render()
        return HTMLResponse(content=error_html)
    if (not is_url_input) and not topic_papers_for_report:
        error_html = render_error_message(
            "No papers were selected to generate a report.",
            context="no_topic_papers_for_report",
        ).render()
        return HTMLResponse(content=error_html)

    # ------------------------------------------------------------------
    # Generate the report via the service layer
    # ------------------------------------------------------------------
    try:
        report_generator = ReportGenerator(
            llm_service=llm_service,
            exa_client=exa_client,
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
        logger.error("report_llm_config_error", error=str(llm_err), **log_params)
        error_html = render_error_message(
            f"LLM Configuration Error: {llm_err}. Please check API keys and model settings.",
            context="llm_config_error_report_gen",
        ).render()
        return HTMLResponse(content=error_html)
    except Exception as exc:  # noqa: BLE001
        logger.exception("report_unexpected_error", error=str(exc), **log_params)
        error_html = render_error_message(
            f"An unexpected error occurred while generating the report: {exc}",
            context="unexpected_report_gen_error",
        ).render()
        return HTMLResponse(content=error_html)


@router.get("/papers/{paper_id:path}/card")
async def citation_card(paper_id: str) -> Response:
    """Return the small hover-card HTML for a citation link."""

    # Placeholder until paper-lookup service exists
    paper = None

    if paper is None:
        return HTMLResponse(
            content=f"<div style='padding:6px;font-size:0.8em;'>Paper {paper_id}</div>"
        )

    return HTMLResponse(content=str(render_paper_card(paper)))
