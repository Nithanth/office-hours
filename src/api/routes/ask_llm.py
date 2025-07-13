"""Minimal `/ask-llm` endpoint - answer a question via the configured LLM."""

import html
from typing import cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client, get_llm_service, get_settings
from config import Config as AppConfig
from infra.exa_client import ExaClient
from services.report_generator import ReportGenerator

router = APIRouter()

# Singleton aliases (initialized during app startup)
exa_client_instance = get_exa_client()
llm_service_instance = get_llm_service()


@router.post("/ask-llm", response_class=HTMLResponse)
async def ask_llm(
    request: Request,
    question: str = Form(...),
    audience: str = Form("technical"),
    model_key: str | None = Form(None),
    summary_size: str = Form("M"),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Direct question-answering using the configured LLM service."""

    logger.info("ask_llm_request", question=question)

    if llm_service_instance is None or not llm_service_instance._initialized:
        logger.error("llm_service_unavailable")
        return HTMLResponse("<p>Server error: LLM service unavailable.</p>", status_code=500)

    selected_key = model_key or settings.DEFAULT_MODEL_KEY
    if selected_key not in settings.AVAILABLE_MODELS:
        logger.warning("ask_llm_unknown_model_key", model_key=selected_key)
        selected_key = settings.DEFAULT_MODEL_KEY
    model_info = settings.AVAILABLE_MODELS[selected_key]

    rg = ReportGenerator(
        llm_service=llm_service_instance,
        exa_client=cast(ExaClient, exa_client_instance),
        selected_model_api_id=model_info["id"],
        selected_model_provider=model_info["provider"],
        selected_model_input_token_limit=model_info["input_token_limit"],
    )

    try:
        size_map = {"S": 256, "M": 512, "L": 1024}
        max_tokens = size_map.get(summary_size.upper(), 512)

        answer_html = await rg.generate_direct_query_answer(
            query=question.strip(), audience_type=audience, max_tokens=max_tokens
        )
        return HTMLResponse(answer_html)
    except Exception as exc:
        logger.exception("ask_llm_failed", error=str(exc))
        err_html = f"<div class='error-banner'>Error: {html.escape(str(exc))}</div>"
        return HTMLResponse(err_html, status_code=500)
