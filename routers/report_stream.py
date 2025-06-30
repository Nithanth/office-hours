"""Incremental report-generation routes using Server-Sent Events (SSE).

This router provides two endpoints:
1. POST /generate-report-incremental — receives the usual form payload, returns
   a lightweight HTML skeleton plus an `hx-ext="sse"` element that connects the
   browser to `/report/stream?...`.
2. GET  /report/stream           — streams each summary block (topic overview,
   main paper, similar papers, topic papers) as soon as it is ready.  Each
   chunk is wrapped in an `hx-swap-oob` directive so HTMX injects it
   immediately, giving the user a block-by-block experience.

The implementation re-uses ReportGenerator._generate_single_summary and keeps
strict ordering by yielding blocks only after they complete, but we push them
as they come via `asyncio.as_completed` for maximal responsiveness.
"""

from __future__ import annotations

import asyncio
import html
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from app import get_session_id
from config import config
from models.paper import Paper
from services.exa_client import ExaClient
from services.llm_client import LLMService
from services.report_generator import ReportGenerator
from utils.observability import structlog
from utils.prompt_templates import AUDIENCE_PROMPT_MAP, get_topic_analysis_prompts

router: APIRouter = APIRouter(tags=["report-incremental"])

REPORT_REQUEST_CACHE: dict[str, dict[str, Any]] = {}

# Max size of each SSE data line – keeping well below 64 KiB browser limit
_SSE_CHUNK_SIZE = 4000  # chars


async def _get_services() -> tuple[LLMService, ExaClient]:
    # Re-use the factories defined in app.py
    from app import get_exa_client, get_llm_service

    llm_service = get_llm_service()
    exa_client = get_exa_client()
    if not llm_service or not exa_client:
        raise HTTPException(status_code=503, detail="Services unavailable")
    return llm_service, exa_client


def _iter_sse_chunks(payload: str) -> list[str]:
    """Yield `data:` lines respecting the browser EventSource line-size limit."""
    return [
        f"data: {payload[i : i + _SSE_CHUNK_SIZE]}" for i in range(0, len(payload), _SSE_CHUNK_SIZE)
    ]


async def _del_cache_later(token: str, delay: int = 60) -> None:
    """Delete a REPORT_REQUEST_CACHE entry after `delay` seconds (grace for reconnects)."""
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
    """Kick off incremental report generation.

    Instead of generating the report synchronously, this returns an SSE hook
    that streams blocks as they are ready.
    """
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

    html_skeleton = f"""
    <div id=\"report-content\" class=\"mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50\">\n      <div id=\"report-progress\"><div class='lux-spinner small' style='display:inline-block;vertical-align:middle;'></div> <span id='report-progress-text'>Writing report…</span></div>\n      <div id=\"report-blocks\"></div>\n      <!-- HTMX SSE connector listening for named events -->\n      <button id=\"copy-report-btn\" class=\"copy-icon-btn\" title=\"Copy text\" type=\"button\"><i class=\"fa-solid fa-copy\"></i></button>\n    </div>\n    <!-- HTMX SSE connector listening for named events -->\n    <div id=\"report-sse-conn\" hx-ext=\"sse\" sse-connect=\"{sse_url}\" hx-sse-reconnect-delay=\"0\" style=\"display:none\">\n      <div sse-swap=\"report-block\" hx-target=\"#report-blocks\" hx-swap=\"beforeend\"></div>\n      <div sse-swap=\"done\" hx-target=\"#report-progress\" hx-swap=\"outerHTML\"></div>\n      <div sse-swap=\"done\" hx-target=\"#report-sse-conn\" hx-swap=\"outerHTML\"></div>\n    </div>\n    """
    return HTMLResponse(content=html_skeleton)


@router.get("/report/stream")
async def report_stream(
    request: Request,
    token: str = Query(...),  # noqa: B008
) -> Response:
    """Stream report blocks as SSE messages identified by a short token."""
    session_id = await get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=400, detail="Session required")

    payload = REPORT_REQUEST_CACHE.get(token)
    if payload is None:
        # Unknown or expired token – send 204 so EventSource treats it as closed and
        # does NOT exponentially retry (avoids endless 404 log spam).
        return Response(status_code=204)

    selected_papers_json = payload["selected_papers_json"]
    audience_type = payload["audience_type"]
    summary_size = payload["summary_size"]
    selected_model_key = payload["selected_model_key"]

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
    model_cfg = config.AVAILABLE_MODELS[selected_model_key]
    rg = ReportGenerator(
        llm_service=llm_service,
        exa_client=exa_client,
        selected_model_api_id=model_cfg["id"],
        selected_model_provider=model_cfg["provider"],
        selected_model_input_token_limit=model_cfg["input_token_limit"],
    )

    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PAPER_SUMMARIES)

    # --- Task helpers -------------------------------------------------

    async def _topic_overview_task() -> tuple[str, str]:
        """LLM call that builds the high-level topic/query overview block."""
        start_ts = time.perf_counter()
        prompt_len_label = summary_size.upper()

        # Length guidance follows mapping logic from ReportGenerator
        topic_size_map = {"S": "M", "M": "L", "L": "XL", "XL": "XL"}
        actual_topic_size_key = topic_size_map.get(prompt_len_label, "L")

        overall_instr = rg.QUALITATIVE_LENGTH_GUIDANCE[actual_topic_size_key]
        # Build a *brief* context – first ~150 chars from first 3 papers
        ctx_parts: list[str] = []
        for p in papers_list[:3]:
            snippet = (p.text_content or "")[:150]
            ctx_parts.append(f"{p.title[:80]} – {snippet}")
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

        structlog.get_logger().info("topic_overview_raw", preview=overview_text[:300])

        dur = time.perf_counter() - start_ts
        structlog.get_logger().info(
            "summary_block_ready", role="topic_overview", duration_sec=round(dur, 2)
        )

        html_block = (
            "<div class='topic-summary'><h3>Overview</h3>"
            f"{rg._text_to_html_paragraphs(overview_text)}</div>"
        )
        return "topic_overview", html_block

    async def _summary_task(paper: Paper, role: str) -> tuple[str, str]:
        # role helps client-side ordering/labeling
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
        structlog.get_logger().info(
            "summary_block_ready",
            role=role,
            paper_id=paper.id,
            duration_sec=round(dur, 2),
        )
        html_block = (
            f"<div class='paper-summary'><h4><a href='{html.escape(str(paper.url) if paper.url else '#')}' target='_blank'>{html.escape(paper.title or 'Paper')}</a></h4>"
            f"{rg._text_to_html_paragraphs(summary)}"
            f"<button class='ask-assistant-btn' hx-get='/chat-modal?paper_id={paper.id}&title={urllib.parse.quote_plus(paper.title or '')}&paper_url={urllib.parse.quote_plus(str(paper.url) if paper.url else '')}' hx-target='#modal-container' hx-swap='innerHTML'>Ask Assistant About This Paper</button>"
            "</div>"
        )
        return role, html_block

    async def event_generator() -> AsyncIterator[str]:
        # Kick off tasks concurrently
        coros: list[asyncio.Task[tuple[str, str]]] = []

        # Derive a reliable boolean – form fields come through as strings.
        is_url_input_flag = str(payload.get("is_url_input", "false")).lower() == "true"

        # 1) Topic overview first (queries only)
        if not is_url_input_flag:
            coros.append(asyncio.create_task(_topic_overview_task()))

        # 2) Main paper summary
        if main_input:
            coros.append(asyncio.create_task(_summary_task(main_input, "main")))

        # 3) Similar papers summaries
        for p in similar_papers:
            coros.append(asyncio.create_task(_summary_task(p, "similar")))

        # Stream blocks **in task creation order** to maintain UI ordering while
        # still allowing the LLM calls to execute concurrently.
        for t in coros:
            _, block_html = await t
            # HTMX SSE extension only swaps the first `data:` line; ensure entire HTML
            # block is delivered in a single line by stripping embedded newlines.
            single_line_html = block_html.replace("\n", " ")
            yield f"event: report-block\ndata: {single_line_html}\n\n"

        # Finalise
        yield "event: done\ndata: \n\n"
        asyncio.create_task(_del_cache_later(token))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
