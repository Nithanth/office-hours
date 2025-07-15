"""Incrementally stream report summaries via Server-Sent Events (SSE).

`POST /generate-report-incremental` returns an HTML shell that connects to
`/report/stream`, and that GET endpoint streams each summary block (topic
overview, main, similar, etc.) as soon as it is ready, improving perceived
latency for the user.
"""

import asyncio
import html
import json
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger
from pydantic import HttpUrl

from api.deps import get_exa_client, get_llm_service, get_session_id
from config import config
from models.paper import Paper
from prompts.prompt_builder import AUDIENCE_PROMPT_MAP, get_topic_analysis_prompts
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMService
from services.report.report_generator import ReportGenerator

router: APIRouter = APIRouter(tags=["report-incremental"])

# Payload values may be str, list[str], or bool – use Any to satisfy mypy.
REPORT_REQUEST_CACHE: dict[str, dict[str, Any]] = {}

# Max size of each SSE data line - keeping well below 64 KiB browser limit
_SSE_CHUNK_SIZE = 4000  # chars


def _format_sse_event(event_name: str, payload: str = "") -> str:
    """Return a browser-safe SSE event string (newline & length-sanitised)."""

    safe_payload = payload.replace("\n", " ")
    data_lines = _iter_sse_chunks(safe_payload) or ["data: "]  # guarantee ≥1 data line

    lines = [f"event: {event_name}", *data_lines, ""]  # final "" adds blank line
    return "\n".join(lines) + "\n"


def _iter_sse_chunks(payload: str) -> list[str]:
    """Yield `data:` lines respecting the browser EventSource line-size limit."""
    return [
        f"data: {payload[i : i + _SSE_CHUNK_SIZE]}" for i in range(0, len(payload), _SSE_CHUNK_SIZE)
    ]


async def _get_services() -> tuple[LLMService, ExaClient]:
    """Return required core services or 503 if unavailable."""

    llm_service = get_llm_service()
    exa_client = get_exa_client()
    if not llm_service or not exa_client:
        raise HTTPException(status_code=503, detail="Services unavailable")
    return llm_service, exa_client


async def _del_cache_later(token: str, delay: int = 60) -> None:
    """Delete *token* from cache after *delay* seconds (gives SSE reconnect grace)."""
    await asyncio.sleep(delay)
    REPORT_REQUEST_CACHE.pop(token, None)


@router.post("/generate-report-incremental", response_class=HTMLResponse)
async def generate_report_incremental(  # noqa: PLR0913
    request: Request,
    original_query_for_report: str = Form(...),  # noqa: B008
    selected_papers_json: list[str] = Form(...),  # noqa: B008
    audience_type: str = Form(...),  # noqa: B008
    summary_size: str = Form(...),  # noqa: B008
    selected_model_key: str = Form(...),  # noqa: B008
    is_url_input: bool = Form(False),  # noqa: B008
) -> HTMLResponse:
    """Return HTML scaffold that connects to live `/report/stream`."""
    # Generate a short-lived token and cache the full request payload server-side.
    token = str(uuid.uuid4())
    REPORT_REQUEST_CACHE[token] = {
        "original_query": original_query_for_report,
        "selected_papers_json": selected_papers_json,
        "audience_type": audience_type,
        "summary_size": summary_size,
        "selected_model_key": selected_model_key,
        "is_url_input": is_url_input,
    }

    sse_url = f"/report/stream?token={token}"

    def _is_blank(val: str | None) -> bool:
        return (val or "").strip() == ""

    if any(_is_blank(v) for v in [audience_type, summary_size, selected_model_key]):
        error_html = (
            "<div class='alert-error' style='color:#d9534f;margin-top:1rem;'>"
            "Please select Audience, Model and Report Size before generating a report."
            "</div>"
        )
        return HTMLResponse(content=error_html)

    if not selected_papers_json:
        error_html = (
            "<div class='alert-error' style='color:#d9534f;margin-top:1rem;'>"
            "Please select at least one paper before generating a report."
            "</div>"
        )
        return HTMLResponse(content=error_html)

    html_skeleton = f"""
    <div id=\"report-content\" class=\"mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50\">\n      <div id=\"report-progress\"><div class='lux-spinner small' style='display:inline-block;vertical-align:middle;'></div> <span id='report-progress-text'>Writing report…</span></div>\n      <div id=\"report-blocks\"></div>\n      <!-- Action buttons hidden until report finished -->\n      <div id=\"report-actions\" class=\"hidden mt-4 pt-4 border-t border-gray-700 flex justify-end gap-2\">\n        <button id=\"report-notes-btn\" class=\"btn-secondary\" title=\"Feature coming soon\" type=\"button\" disabled>\n          <i class=\"fa-solid fa-wand-magic-sparkles\"></i>&nbsp;Generate Notes
        </button>\n        <button id=\"copy-report-btn\" class=\"copy-icon-btn\" title=\"Copy text\" type=\"button\">
          <i class=\"fa-solid fa-copy\"></i>
        </button>\n      </div>\n    </div>\n    <!-- HTMX SSE connector listening for named events -->\n    <div id=\"report-sse-conn\" hx-ext=\"sse\" sse-connect=\"{sse_url}\" hx-sse-reconnect-delay=\"0\" style=\"display:none\">\n      <div sse-swap=\"report-block\" hx-target=\"#report-blocks\" hx-swap=\"beforeend\"></div>\n      <div sse-swap=\"done\" hx-target=\"#report-progress\" hx-swap=\"outerHTML\"></div>\n      <!-- Action buttons stay; report.js will unhide them on done -->
      <div sse-swap=\"done\" hx-target=\"#report-sse-conn\" hx-swap=\"outerHTML\"></div>\n    </div>
    """
    return HTMLResponse(content=html_skeleton)


@router.get("/report/stream")
async def report_stream(
    request: Request,
    token: str = Query(...),  # noqa: B008
) -> Response:
    """Stream report blocks for the cached *token*."""
    session_id = await get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=400, detail="Session required")

    payload = REPORT_REQUEST_CACHE.get(token)
    if payload is None:
        # Unknown or expired token - send 204 so EventSource treats it as closed and
        # does NOT exponentially retry (avoids endless 404 log spam).
        return Response(status_code=204)

    selected_papers_json = payload["selected_papers_json"]
    audience_type = payload["audience_type"]
    summary_size = payload["summary_size"]
    selected_model_key = payload["selected_model_key"] or config.DEFAULT_MODEL_KEY

    llm_service, exa_client = await _get_services()

    # Deserialize selected papers
    papers_list: list[Paper] = []
    for p_json in selected_papers_json:
        try:
            papers_list.append(Paper.model_validate_json(p_json))
        except Exception:  # noqa: BLE001
            continue

    # Derive main/ similar/ topic papers same as synchronous path — here we
    # assume the first paper (if any) is the main; rest are similar.
    main_input = papers_list[0] if papers_list else None
    similar_papers = papers_list[1:]

    # Prepare ReportGenerator
    model_cfg = config.AVAILABLE_MODELS.get(selected_model_key)
    if model_cfg is None:
        error_html = (
            "<div class='alert-error' style='color:#d9534f;margin-top:1rem;'>"
            "Unknown model selected. Please choose a valid model and retry."
            "</div>"
        )
        return HTMLResponse(content=error_html, status_code=200)
    rg = ReportGenerator(
        llm_service=llm_service,
        exa_client=exa_client,
        selected_model_api_id=model_cfg["id"],
        selected_model_provider=model_cfg["provider"],
        selected_model_input_token_limit=model_cfg["input_token_limit"],
    )

    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PAPER_SUMMARIES)

    async def _topic_overview_task() -> tuple[str, str]:
        """LLM call that builds the high-level topic/query overview block."""
        start_ts = time.perf_counter()
        prompt_len_label = summary_size.upper()

        # Topic summary now respects the same S/M/L sizing
        actual_topic_size_key = prompt_len_label if prompt_len_label in ("S", "M", "L") else "M"

        overall_instr = rg.QUALITATIVE_LENGTH_GUIDANCE[actual_topic_size_key]
        # Build a *brief* context - first ~150 chars from first 3 papers
        ctx_parts: list[str] = []
        for p in papers_list[:3]:
            snippet = (p.text_content or "")[:150]
            ctx_parts.append(f"{p.title[:80]} - {snippet}")
        context_snippets = "\n".join(ctx_parts)

        sys_prompt, user_prompt = get_topic_analysis_prompts(
            topic=payload["original_query"],
            num_papers_to_synthesize=len(papers_list) or 3,
            target_audience=audience_type,
            context_snippets=context_snippets,
            overall_instructions=overall_instr,
        )

        tok_target = rg._get_output_token_target(
            audience_type, actual_topic_size_key, is_topic_summary=True
        )
        tok_target = min(tok_target, config.MAX_TOPIC_SUMMARY_TOKENS)

        overview_text = await rg._generate_single_summary(
            user_prompt,
            tok_target,
            system_prompt=sys_prompt,
        )

        logger.info("topic_overview_raw", preview=overview_text[:300])

        dur = time.perf_counter() - start_ts
        logger.info("summary_block_ready", role="topic_overview", duration_sec=round(dur, 2))

        html_block = (
            "<div class='topic-summary'><h3>Overview</h3>"
            f"{rg._text_to_html_paragraphs(overview_text)}</div>"
        )
        return "topic_overview", html_block

    async def _summary_task(paper: Paper, role: str) -> tuple[str, str]:
        """LLM call that builds a summary for *paper* (role = main|similar)."""
        start_ts = time.perf_counter()
        prompt_len_label = summary_size.upper()
        tok_target = rg._get_output_token_target(audience_type, prompt_len_label, False)
        snippet = (paper.text_content or "")[
            : rg._get_max_content_snippet_chars(audience_type, prompt_len_label, False)
        ]
        active_paper_prompt_template = AUDIENCE_PROMPT_MAP.get(
            audience_type, AUDIENCE_PROMPT_MAP["technical"]
        )["paper"]
        prompt = active_paper_prompt_template.format(
            paper_title=html.escape(paper.title or "N/A"),
            paper_authors=html.escape(", ".join(paper.authors) if paper.authors else "N/A"),
            paper_publication_date=html.escape(rg._get_formatted_date_str(paper.published_date)),
            paper_content_snippet=snippet,
            overall_instructions=rg.QUALITATIVE_LENGTH_GUIDANCE[prompt_len_label],
            target_audience=audience_type,
        )
        summary = await rg._generate_single_summary(prompt, tok_target, semaphore=semaphore)
        dur = time.perf_counter() - start_ts
        logger.info(
            "summary_block_ready",
            role=role,
            paper_id=paper.id,
            duration_sec=round(dur, 2),
        )
        html_block = (
            f"<div class='paper-summary'><h4><a href='{html.escape(str(paper.url) if paper.url else '#')}' target='_blank'>{html.escape(paper.title or 'Paper')}</a></h4>"
            f"{rg._text_to_html_paragraphs(summary)}"
            f"<button class='ask-assistant-btn' hx-get='/chat-modal?paper_id={paper.id}&title={urllib.parse.quote_plus(paper.title or '')}&paper_url={urllib.parse.quote_plus(str(paper.url) if paper.url else '')}&audience_type={urllib.parse.quote_plus(audience_type)}' hx-target='#modal-container' hx-swap='innerHTML'>Ask Assistant About This Paper</button>"
            "</div>"
        )
        return role, html_block

    async def event_generator() -> AsyncIterator[str]:
        # Kick off tasks concurrently
        coros: list[asyncio.Task[tuple[str, str]]] = []
        task_paper_map: dict[asyncio.Task, Paper] = {}
        failed_titles: list[str] = []

        # --- Emit heading block first so it appears above all summaries ---
        heading_html = "<h2 class='report-title text-xl font-bold mb-4'>Report</h2>"
        yield _format_sse_event("report-block", heading_html)

        # Derive a reliable boolean - form fields come through as strings.
        is_url_input_flag = str(payload.get("is_url_input", "false")).lower() == "true"

        # 1) Topic overview first (queries only)
        if not is_url_input_flag:
            coros.append(asyncio.create_task(_topic_overview_task()))

        # 2) Main paper summary
        if main_input:
            t_main = asyncio.create_task(_summary_task(main_input, "main"))
            coros.append(t_main)
            task_paper_map[t_main] = main_input

        # 3) Similar papers summaries
        for p in similar_papers:
            t_sim = asyncio.create_task(_summary_task(p, "similar"))
            coros.append(t_sim)
            task_paper_map[t_sim] = p

        # Stream blocks **in task creation order** to maintain UI ordering while
        # still allowing the LLM calls to execute concurrently.
        for t in coros:
            try:
                _role_label, block_html = await t
            except Exception as e:  # noqa: BLE001
                logger.warning("stream_block_error", exc=str(e))
                failed_titles.append(
                    (
                        task_paper_map.get(t)
                        or Paper(
                            id="N/A",
                            title="Unknown",
                            url=cast(HttpUrl, "https://example.com/unknown"),
                        )
                    ).title
                )
                continue  # skip yielding block for failed paper

            # Successful - stream block
            yield _format_sse_event("report-block", block_html)

        # Inform client of any failures via a dedicated event **before** done
        if failed_titles:
            yield _format_sse_event("failures", json.dumps(failed_titles))

        # Finalise - send empty payload
        yield _format_sse_event("done", "")

        await _del_cache_later(token)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
