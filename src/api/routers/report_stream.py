"""Incrementally stream report summaries via Server-Sent Events (SSE).

`POST /generate-report-incremental` returns an HTML shell that connects to
`/report/stream`, and that GET endpoint streams each summary block (topic
overview, main, similar, etc.) as soon as it is ready, improving perceived
latency for the user.
"""

import asyncio
import html
import json
import re
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger

from api.deps import get_exa_client, get_llm_service, get_session_id
from config import config
from models.paper import Paper
from prompts.prompt_builder import AUDIENCE_PROMPT_MAP, get_topic_analysis_prompts
from services.core import model_prefs
from services.core.exa_client import ExaClient
from services.core.exa_utils import paper_utils as _paper_utils
from services.core.llm_service import LLMService
from services.report.report_generator import ReportGenerator
from utils.observability import _METRICS_ENABLED, REPORT_BLOCKS, REPORT_LAT, REPORT_REQ
from utils.rate_limit import limiter

router: APIRouter = APIRouter(tags=["report-incremental"])

# Payload values may be str, list[str], or bool – use Any to satisfy mypy.
REPORT_REQUEST_CACHE: dict[str, dict[str, Any]] = {}
_REPORT_REQUEST_DIR = Path(config.CACHE_DIR) / "report_requests"
# Safety-cleanup tasks keyed by token so we can cancel them once the stream starts.
_cleanup_tasks: dict[str, asyncio.Task[None]] = {}

# Detached report-generation jobs keyed by token. The SSE handler only
# drains a queue — the generator runs in its own task so navigating away
# mid-report can't cancel it; the report still persists to My Reports.
_REPORT_JOB_QUEUES: dict[str, asyncio.Queue[str | None]] = {}
_REPORT_JOB_TASKS: dict[str, asyncio.Task[None]] = {}
_REPORT_JOB_GC_DELAY = 300  # seconds a finished job stays re-drainable

_JOB_SENTINEL = None


async def _gc_report_job(token: str) -> None:
    await asyncio.sleep(_REPORT_JOB_GC_DELAY)
    _REPORT_JOB_QUEUES.pop(token, None)
    _REPORT_JOB_TASKS.pop(token, None)


async def _pump_report_gen(
    token: str, agen: AsyncIterator[str], queue: asyncio.Queue[str | None]
) -> None:
    """Feed the report generator's SSE frames into *queue* until exhausted.

    Runs as its own task: a client disconnect cancels the draining response,
    not this pump, so generation and DB persistence always complete.
    """
    try:
        async for item in agen:
            await queue.put(item)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("report_job_pump_failed", token=token)
    finally:
        await queue.put(_JOB_SENTINEL)
        asyncio.create_task(_gc_report_job(token))


def _drain_job_queue(
    queue: asyncio.Queue[str | None],
) -> AsyncIterator[str]:
    """Yield queued SSE frames until the job's sentinel."""

    async def _gen() -> AsyncIterator[str]:
        while True:
            item = await queue.get()
            if item is _JOB_SENTINEL:
                # Re-arm the sentinel so a later reconnect sees a clean end.
                await queue.put(_JOB_SENTINEL)
                return
            yield item

    return _gen()


# Max size of each SSE data line - keeping well below 64 KiB browser limit
_SSE_CHUNK_SIZE = 4000  # chars

# Name of the extra SSE event carrying JSON envelope of each block.
_JSON_EVENT = "report-block-json"


def _clean_generated_title(raw: str | None) -> str:
    """Normalize an LLM title; '' if it looks like a truncated fragment.

    Observed bad outputs: '(their', '(' — the 30-token cap cutting a
    parenthesized draft mid-word. We strip TITLE: prefixes, markdown
    emphasis, quotes, and wrapping parens, then reject anything that still
    looks like a fragment.
    """
    if not raw:
        return ""
    t = re.sub(r"(?i)^\s*title\s*[:\-]?\s*", "", raw.strip())
    t = t.strip().lstrip("#").strip().strip('*"`').strip()
    # unwrap one level of enclosing parens/quotes/brackets
    for open_c, close_c in (("(", ")"), ("[", "]"), ("'", "'")):
        if t.startswith(open_c) and t.endswith(close_c):
            t = t[1:-1].strip()
    # reject fragments: unclosed paren, too short, or no letters at all
    if t.count("(") != t.count(")"):
        return ""
    if len(t) < 8 or not re.search(r"[A-Za-z]", t):
        return ""
    return t


def _report_request_path(token: str) -> Path:
    safe = "".join(c for c in token if c.isalnum() or c in ("-", "_"))
    return _REPORT_REQUEST_DIR / f"{safe}.json"


def _save_report_request(token: str, payload: dict[str, Any]) -> None:
    """Persist report payload so SSE can resume across workers/restarts."""

    try:
        _REPORT_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
        path = _report_request_path(token)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as exc:
        logger.warning("report_request_cache_write_failed", token=token, error=str(exc))


def _load_report_request(token: str) -> dict[str, Any] | None:
    payload = REPORT_REQUEST_CACHE.get(token)
    if payload is not None:
        return payload

    try:
        path = _report_request_path(token)
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return None
        REPORT_REQUEST_CACHE[token] = loaded
        return loaded
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("report_request_cache_read_failed", token=token, error=str(exc))
        return None


def _delete_report_request(token: str) -> None:
    REPORT_REQUEST_CACHE.pop(token, None)
    try:
        _report_request_path(token).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("report_request_cache_delete_failed", token=token, error=str(exc))


def _format_sse_event(event_name: str, payload: str = "") -> str:
    """Return a browser-safe SSE event string (newline & length-sanitised)."""

    safe_payload = payload.replace("\n", " ")
    data_lines = _iter_sse_chunks(safe_payload) or ["data: "]  # guarantee ≥1 data line

    lines = [f"event: {event_name}", *data_lines, ""]  # final "" adds blank line
    return "\n".join(lines) + "\n"


def _iter_sse_chunks(payload: str) -> list[str]:
    """Yield `data:` lines respecting the browser EventSource line-size limit."""
    return [
        f"data: {payload[i : i + _SSE_CHUNK_SIZE]}"
        for i in range(0, len(payload), _SSE_CHUNK_SIZE)
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
    _delete_report_request(token)


@limiter.limit("20/5minute")
@router.post("/generate-report-incremental", response_class=HTMLResponse)
async def generate_report_incremental(  # noqa: PLR0913
    request: Request,
    original_query_for_report: str = Form(...),  # noqa: B008
    selected_papers_json: list[str] = Form(...),  # noqa: B008
    audience_type: str = Form(...),  # noqa: B008
    summary_size: str = Form(...),  # noqa: B008
    selected_model_key: str = Form(...),  # noqa: B008
    is_url_input: bool = Form(False),  # noqa: B008
    forced_topic: bool = Form(False),  # noqa: B008
) -> HTMLResponse:
    """Return HTML scaffold that connects to live `/report/stream`."""
    from services.core.model_prefs import prefs_owner_id
    from services.limits import check_daily_limit
    from services.persistence.database import database_available, session_scope

    # Owner key: auth user_id when signed in, else "sess:{id}" — anonymous
    # visitors own (and are rate-limited on) their reports too. A BYOK
    # OpenRouter key lifts the cap; BETA_EXEMPT_USER_IDS bypass it.
    user_id = prefs_owner_id(request)
    limit_msg = await check_daily_limit(request, "report_generated")
    if limit_msg:
        return HTMLResponse(
            "<div class='alert-error' style='color:#d9534f;margin-top:1rem;'>"
            f"{limit_msg}"
            "</div>",
            status_code=429,
        )

    # Metrics: count incoming requests
    if _METRICS_ENABLED and REPORT_REQ is not None:
        REPORT_REQ.inc()

    # --- Validate before caching ---
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

    resolved_model_key = config.resolve_model_key(selected_model_key)
    if resolved_model_key is None:
        error_html = (
            "<div class='alert-error' style='color:#d9534f;margin-top:1rem;'>"
            "Unknown model selected. Please choose a valid model and retry."
            "</div>"
        )
        return HTMLResponse(content=error_html)
    selected_model_key = resolved_model_key

    # Resolve-or-create the topic this report is a snapshot of — the topic is
    # the unit; the query is only its seed.
    topic_id: str | None = None
    if user_id and database_available():
        from services.persistence.repositories import get_or_create_topic
        from services.topics.keys import canonical_key_for, detect_topic_kind

        kind = await asyncio.to_thread(
            detect_topic_kind,
            original_query_for_report,
            is_url_input=bool(is_url_input),
            confirm_bare_name=True,
            subject_forced=forced_topic,
        )
        ckey = canonical_key_for(original_query_for_report, kind=kind)
        if ckey:
            async for session in session_scope():
                topic, _created = await get_or_create_topic(
                    session,
                    owner_id=user_id,
                    kind=kind,
                    canonical_key=ckey,
                    title=original_query_for_report,
                    seed_query=original_query_for_report,
                )
                from services.persistence.repositories import ensure_seed_facet

                await ensure_seed_facet(session, topic=topic)
                await session.commit()
                topic_id = topic.id
                await session.commit()

    # --- Cache payload and generate token (only after validation) ---
    token = str(uuid.uuid4())
    payload = {
        "original_query": original_query_for_report,
        "selected_papers_json": selected_papers_json,
        "audience_type": audience_type,
        "summary_size": summary_size,
        "selected_model_key": selected_model_key,
        "is_url_input": is_url_input,
        "user_id": user_id,
        "topic_id": topic_id,
    }
    REPORT_REQUEST_CACHE[token] = payload
    _save_report_request(token, payload)

    logger.info(
        "report_incremental_received", token=token, is_url_input_form=is_url_input
    )

    # Schedule cleanup in case the SSE stream never connects.
    # The task is stored so report_stream() can cancel it once the stream starts.
    _cleanup_tasks[token] = asyncio.create_task(_del_cache_later(token, delay=120))

    return HTMLResponse(content=_report_skeleton_html(token))


def _report_skeleton_html(token: str) -> str:
    """Streaming skeleton for the SSE report generator — shared by the
    generate endpoint and the topic re-render endpoint."""
    sse_url = f"/report/stream?token={token}"
    return f"""
    <div id=\"report-content\" class=\"mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50\">\n      <!-- View toggle revealed once the report is persisted (report.js unhides it on report-saved) -->\n      <div id=\"report-view-toggle\" class=\"oh-view-toggle hidden\">\n        <button type=\"button\" data-view=\"report\" class=\"active\"><i class=\"fa-regular fa-file-lines\"></i>&nbsp;Report</button>\n        <button type=\"button\" data-view=\"blog\"><i class=\"fa-solid fa-pen-nib\"></i>&nbsp;Blog</button>\n      </div>\n      <div id=\"report-progress\"><div class='oh-spinner small' style='display:inline-block;vertical-align:middle;'></div> <span id='report-progress-text'>Writing report…</span></div>\n      <div id=\"report-blocks\"></div>\n      <!-- Action buttons hidden until report finished -->\n      <div id=\"report-actions\" class=\"hidden mt-4 pt-4 border-t border-gray-700 flex flex-wrap justify-end items-center gap-2\">\n        <button id=\"report-notes-btn\" class=\"btn-secondary\" title=\"Generate study notes\" type=\"button\" disabled>\n          <i class=\"fa-solid fa-wand-magic-sparkles\"></i>&nbsp;Notes
        </button>\n        <button id=\"report-blog-btn\" class=\"btn-secondary\" title=\"Generate blog post\" type=\"button\" disabled>\n          <i class=\"fa-solid fa-pen-nib\"></i>&nbsp;Blog
        </button>\n        <button id=\"report-topic-chat-btn\" class=\"btn-secondary ask-assistant-btn\" title=\"Chat across all papers in this topic\" type=\"button\" disabled\n          hx-get=\"/viewer/topic?title=Ask%20about%20this%20topic\" hx-target=\"#modal-container\" hx-swap=\"innerHTML\">\n          <i class=\"fa-solid fa-comments\"></i>&nbsp;Topic chat
        </button>\n        <button id=\"copy-report-btn\" class=\"copy-icon-btn\" title=\"Copy text\" type=\"button\">
          <i class=\"fa-solid fa-copy\"></i>
        </button>\n      </div>\n    </div>\n    <!-- HTMX SSE connector listening for named events -->\n    <div id=\"report-sse-conn\" hx-ext=\"sse\" sse-connect=\"{sse_url}\" hx-sse-reconnect-delay=\"0\" style=\"display:none\">\n      <div sse-swap=\"report-block\" hx-target=\"#report-blocks\" hx-swap=\"beforeend\"></div>\n      <div sse-swap=\"report-progress\" hx-target=\"#report-progress\" hx-swap=\"innerHTML\"></div>\n      <div sse-swap=\"done\" hx-target=\"#report-progress\" hx-swap=\"outerHTML\"></div>\n      <!-- Action buttons stay; report.js will unhide them on done -->
      <div sse-swap=\"done\" hx-target=\"#report-sse-conn\" hx-swap=\"outerHTML\"></div>\n      <!-- failed: replaces the spinner inside #report-progress (element stays, so report.js never treats the report as finished) and tears down the connector -->\n      <div sse-swap=\"failed\" hx-target=\"#report-progress\" hx-swap=\"innerHTML\"></div>\n      <div sse-swap=\"failed\" hx-target=\"#report-sse-conn\" hx-swap=\"outerHTML\"></div>\n      <!-- Registers the report-saved event so htmx dispatches htmx:sseMessage;\n           report.js uses it to append report_id= to the chat buttons. -->\n      <div sse-swap=\"report-saved\" hx-target=\"this\" hx-swap=\"none\"></div>\n    </div>
    """


def cache_report_request(
    payload: dict[str, Any],
) -> str:
    """Cache a report payload under a fresh token + schedule safety cleanup.

    Shared with the topic re-render endpoint, which builds the same payload
    server-side instead of via the generate form.
    """
    token = str(uuid.uuid4())
    REPORT_REQUEST_CACHE[token] = payload
    _save_report_request(token, payload)
    _cleanup_tasks[token] = asyncio.create_task(_del_cache_later(token, delay=120))
    return token


@router.get("/report/stream")
async def report_stream(
    request: Request,
    token: str = Query(...),  # noqa: B008
) -> Response:
    """Stream report blocks for the cached *token*."""
    session_id = await get_session_id(request)
    if not session_id:
        raise HTTPException(status_code=400, detail="Session required")

    payload = _load_report_request(token)
    if payload is None:
        # If the report job is already running (client navigated away and
        # came back), re-attach by draining its queue — earlier events were
        # consumed by the original connection but the page re-renders them
        # from the saved report anyway.
        job_queue = _REPORT_JOB_QUEUES.get(token)
        job_task = _REPORT_JOB_TASKS.get(token)
        if job_queue is not None and job_task is not None and not job_task.done():
            return StreamingResponse(
                _drain_job_queue(job_queue), media_type="text/event-stream"
            )

        # Unknown, expired, or already-claimed token — emit a terminal SSE so
        # the reconnect loop stops (the 'done' event swaps the connector out
        # of the DOM) and the spinner resolves to a visible message instead
        # of hanging forever on 204s.
        async def _terminal_stream() -> AsyncIterator[str]:
            yield _format_sse_event(
                "report-block",
                "<div class='alert-error' style='color:#d9534f;'>"
                "Report already generated or this link expired. "
                "Refresh the page to see the saved report.</div>",
            )
            yield _format_sse_event("done")

        return StreamingResponse(_terminal_stream(), media_type="text/event-stream")

    # Cancel the safety-cleanup timer now that the stream has connected.
    safety_task = _cleanup_tasks.pop(token, None)
    if safety_task and not safety_task.done():
        safety_task.cancel()

    # One-shot claim: the EventSource reconnects when the stream closes
    # (hx-sse-reconnect-delay=0), and a reconnect that still finds the payload
    # re-runs the whole generator — persisting a duplicate report. Delete the
    # payload on first connect so any later request gets a clean 204. The
    # running generator keeps the payload in memory and finishes/persists
    # normally, so a finished report still lands in "My Reports".
    _delete_report_request(token)

    selected_papers_json = payload["selected_papers_json"]
    audience_type = payload["audience_type"]
    summary_size = payload["summary_size"]
    selected_model_key = payload["selected_model_key"] or config.DEFAULT_MODEL_KEY
    user_id = payload.get("user_id")

    llm_service, exa_client = await _get_services()

    # Deserialize selected papers
    papers_list: list[Paper] = []
    for p_json in selected_papers_json:
        try:
            papers_list.append(Paper.model_validate_json(p_json))
        except Exception:  # noqa: BLE001
            logger.warning("report_paper_deserialize_skip", raw=p_json[:200])
            continue

    if not papers_list:
        logger.error("report_stream_no_valid_papers", token=token)
        return HTMLResponse(
            "<p class='oh-toast' style='padding:12px;'>No valid papers could be loaded. "
            "Please go back and try again.</p>",
            status_code=422,
        )

    # Derive main/ similar/ topic papers same as synchronous path — here we
    # assume the first paper (if any) is the main; rest are similar.
    main_input = papers_list[0] if papers_list else None
    similar_papers = papers_list[1:]

    # Ensure we have full-text content for all papers (Exa only gives
    # abstracts). Re-use the same TextExtractor logic as synchronous
    # ReportGenerator so the char stats and summariser get full PDFs.
    try:
        from services.paper.text_extractor import (
            TextExtractor,
        )  # local import to avoid heavy dep if not needed

        extractor = TextExtractor.instance()
        sem = asyncio.Semaphore(3)

        async def _fetch_full(p: Paper) -> None:
            async with sem:
                # prefer_exa=True keeps the Exa-contents fallback layer enabled:
                # it only counts as partial, so real PDFs still win, but
                # paywalled/blocked pages (e.g. ACM DOIs) still get indexed
                # text — which is what topic-chat retrieval draws on.
                path = await extractor.ensure_cached(str(p.url), p.id, prefer_exa=True)
                if path:
                    try:
                        txt = Path(path).read_text(encoding="utf-8")
                    except Exception:
                        txt = await extractor.read_full_text(p.id)  # fallback glob
                    if txt and len(txt) > len(p.text_content or ""):
                        p.text_content = txt
                        logger.info(
                            "full_text_cached",
                            paper_id=p.id,
                            chars=len(txt),
                            prev_chars=len(p.text_content) if p.text_content else 0,
                        )

        await asyncio.gather(*[_fetch_full(p) for p in papers_list])
    except Exception as _extract_err:  # pragma: no cover – robustness only
        logger.debug("full_text_prefetch_failed", error=str(_extract_err))

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
        selected_model_api_id=model_prefs.resolve_model_id(selected_model_key),
        selected_model_provider=model_cfg["provider"],
        selected_model_input_token_limit=model_cfg["input_token_limit"],
    )

    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PAPER_SUMMARIES)

    async def _topic_overview_task() -> tuple[str, str]:
        """LLM call that builds the high-level topic/query overview block."""
        start_ts = time.perf_counter()
        prompt_len_label = summary_size.upper()

        topic_size_map = {"S": "M", "M": "L", "L": "L", "XL": "L"}
        actual_topic_size_key = topic_size_map.get(prompt_len_label, "M")

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
        logger.info(
            "summary_block_ready", role="topic_overview", duration_sec=round(dur, 2)
        )

        html_block = (
            "<div class='topic-summary'><h3>Overview</h3>"
            f"{rg._text_to_html_paragraphs(overview_text)}</div>"
        )
        return "topic_overview", html_block

    async def _summary_task(paper: Paper, role: str) -> tuple[str, str]:
        """LLM call that builds a summary for *paper* (role = main|similar)."""
        start_ts = time.perf_counter()
        prompt_len_label = summary_size.upper()
        normalized_size = (
            prompt_len_label
            if prompt_len_label in rg.QUALITATIVE_LENGTH_GUIDANCE
            else "M"
        )
        tok_target = rg._get_output_token_target(audience_type, normalized_size, False)
        snippet_limit = rg._get_max_content_snippet_chars(
            audience_type, normalized_size, False
        )
        full_text = paper.text_content or ""
        snippet = full_text[:snippet_limit]

        # Log char stats at INFO level (parity with full report generator)
        logger.info(
            f"report_paper_char_stats: id={paper.id} total={len(full_text)} used={len(snippet)}/{snippet_limit}",
            paper_id=paper.id,
            total_chars=len(full_text),
            snippet_used=len(snippet),
            snippet_limit=snippet_limit,
        )

        active_paper_prompt_template = AUDIENCE_PROMPT_MAP.get(
            audience_type, AUDIENCE_PROMPT_MAP["technical"]
        )["paper"]
        prompt = active_paper_prompt_template.format(
            paper_title=html.escape(paper.title or "N/A"),
            paper_authors=html.escape(
                ", ".join(paper.authors) if paper.authors else "N/A"
            ),
            paper_publication_date=html.escape(
                rg._get_formatted_date_str(paper.published_date)
            ),
            paper_content_snippet=snippet,
            overall_instructions=rg.QUALITATIVE_LENGTH_GUIDANCE[normalized_size],
            target_audience=audience_type,
        )
        summary = await rg._generate_single_summary(
            prompt, tok_target, semaphore=semaphore
        )
        dur = time.perf_counter() - start_ts
        logger.info(
            "summary_block_ready",
            role=role,
            paper_id=paper.id,
            duration_sec=round(dur, 2),
        )
        html_block = (
            f"<div class='paper-summary'><h4><a href='{html.escape(str(paper.url) if paper.url else '#')}' target='_blank' rel='noopener noreferrer'>{html.escape(paper.title or 'Paper')}</a></h4>"
            f"{rg._text_to_html_paragraphs(summary)}"
            f"<button class='ask-assistant-btn' style='position:static;display:inline-block;margin-top:0.75rem;' hx-get='/viewer/{paper.id}?url={urllib.parse.quote_plus(str(paper.url) if paper.url else '')}&title={urllib.parse.quote_plus(paper.title or '')}&audience_type={urllib.parse.quote_plus(audience_type)}&model_key={urllib.parse.quote_plus(selected_model_key)}' hx-target='#modal-container' hx-swap='innerHTML'>Ask Assistant About This Paper</button>"
            "</div>"
        )
        return role, html_block

    async def event_generator() -> AsyncIterator[str]:
        _start_time = time.perf_counter()  # latency metric
        persisted_blocks: list[str] = []
        # Kick off tasks concurrently
        coros: list[asyncio.Task[tuple[str, str]]] = []
        task_paper_map: dict[asyncio.Task, Paper] = {}
        failed_titles: list[str] = []

        # --- Emit heading block first so it appears above all summaries ---
        heading_html = "<h2 class='report-title text-xl font-bold mb-4'>Report</h2>"
        persisted_blocks.append(heading_html)
        yield _format_sse_event("report-block", heading_html)
        # JSON envelope for clients needing structured stream (non-breaking)
        heading_meta = json.dumps(
            {
                "block_type": "heading",
                "html": heading_html,
                "timestamp": int(time.time()),
            },
            separators=(",", ":"),
        )
        yield _format_sse_event(_JSON_EVENT, heading_meta)

        # Derive a reliable boolean - form fields come through as strings.
        is_url_input_flag = str(payload.get("is_url_input", "false")).lower() == "true"

        logger.debug(
            "stream_report_flags",
            is_url_input_raw=payload.get("is_url_input"),
            is_url_input_flag=is_url_input_flag,
            will_add_topic_overview=not is_url_input_flag,
        )

        # 1) Topic overview first (queries only)
        if not is_url_input_flag:
            logger.info("stream_adding_topic_overview")
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
        total_blocks = len(coros)
        for block_idx, t in enumerate(coros, 1):
            # Send progress update
            progress_text = f"Summarizing {block_idx} of {total_blocks}…"
            yield _format_sse_event(
                "report-progress",
                f"<div class='oh-spinner small' style='display:inline-block;vertical-align:middle;'></div> <span id='report-progress-text'>{progress_text}</span>",
            )
            try:
                _role_label, block_html = await t
            except (TimeoutError, ValueError, RuntimeError) as e:
                logger.warning("stream_block_error", exc=str(e))
                failed_titles.append(
                    (
                        task_paper_map.get(t)
                        or Paper(
                            id="N/A",
                            title="Unknown",
                            url=_paper_utils._coerce_http_url(
                                "https://example.com/unknown"
                            ),
                        )
                    ).title
                )
                continue  # skip yielding block for failed paper

            # Successful - stream block
            if _METRICS_ENABLED and REPORT_BLOCKS is not None:
                REPORT_BLOCKS.inc()
            # Send HTML block for existing HTMX swap
            persisted_blocks.append(block_html)
            yield _format_sse_event("report-block", block_html)
            # Send JSON envelope alongside for consumers needing structure
            block_meta = json.dumps(
                {
                    "block_type": (
                        _role_label if _role_label != "main" else "paper_summary"
                    ),
                    "paper_id": getattr(task_paper_map.get(t), "id", None),
                    "source": (
                        "url_input"
                        if (_role_label == "main" and is_url_input_flag)
                        else _role_label
                    ),
                    "html": block_html,
                    "timestamp": int(time.time()),
                },
                separators=(",", ":"),
            )
            yield _format_sse_event(_JSON_EVENT, block_meta)

        # Inform client of any failures via a dedicated event **before** done
        if failed_titles:
            yield _format_sse_event("failures", json.dumps(failed_titles))

        # Nothing generated beyond the heading — replace the spinner with an
        # error card via the dedicated 'failed' event (never 'done', which would
        # reveal report actions + a success toast), and don't persist.
        if len(persisted_blocks) <= 1:
            yield _format_sse_event(
                "failed",
                "<div class='alert-error' style='color:#d9534f;'>"
                "Report generation failed — the model provider returned an error "
                "for every section. Check your provider key or try another model."
                "</div>",
            )
            yield _format_sse_event(f"{_JSON_EVENT}-done", "")
            return

        # Finalise - persist report to database
        if user_id:
            try:
                from services.persistence.database import (
                    database_available,
                    session_scope,
                )
                from services.persistence.repositories import create_report

                if database_available():
                    # Generate a concise title from the report content.
                    report_title = (
                        str(payload["original_query"])[:300] or "Research Report"
                    )
                    try:
                        report_body = "\n".join(persisted_blocks)
                        title_result = await rg._generate_report_title(
                            report_body, selected_model_key
                        )
                        cleaned = _clean_generated_title(title_result)
                        if cleaned:
                            report_title = cleaned[:300]
                    except Exception:  # noqa: BLE001 - title gen is best-effort
                        pass

                    saved = None
                    topic_id = payload.get("topic_id")
                    async for session in session_scope():
                        saved = await create_report(
                            session,
                            user_id=str(user_id),
                            title=report_title,
                            original_query=str(payload["original_query"]),
                            audience_type=audience_type,
                            summary_size=summary_size,
                            model_key=selected_model_key,
                            is_url_input=is_url_input_flag,
                            report_html="\n".join(persisted_blocks),
                            papers=papers_list,
                            topic_id=topic_id,
                        )
                        if topic_id:
                            from services.persistence.repositories import (
                                add_topic_papers,
                            )

                            await add_topic_papers(
                                session,
                                topic_id=topic_id,
                                papers=papers_list,
                                source="seed",
                            )
                            await session.commit()
                    if saved is not None:
                        yield _format_sse_event(
                            "report-saved",
                            json.dumps({"report_id": saved.id}, separators=(",", ":")),
                        )
                        logger.info(
                            "report_persisted", report_id=saved.id, user_id=user_id
                        )
                        try:
                            from api.routes.library import (
                                schedule_notion_workspace_sync,
                            )

                            schedule_notion_workspace_sync(str(user_id))
                        except Exception:  # noqa: BLE001 - sync is best-effort
                            pass
            except (
                Exception
            ) as exc:  # noqa: BLE001 - persistence must not break streaming
                logger.warning("report_persist_failed", user_id=user_id, error=str(exc))
                yield _format_sse_event(
                    "report-save-error",
                    "Report generated but could not be saved. You can still export it.",
                )

        # The done payload replaces #report-progress — an inline <script> runs
        # post-swap (htmx evaluates scripts in swapped content) to typeset math
        # in the finished report.
        yield _format_sse_event(
            "done",
            "<script>var r=window.ohBlogRender;r&&r.loadAllDeps().then(function(){"
            "r.renderKaTeX(document.getElementById('report-content'));});</script>",
        )
        # Also signal done for JSON stream so any listener can clean up.
        yield _format_sse_event(f"{_JSON_EVENT}-done", "")

        # Observe overall latency once all blocks sent
        if _METRICS_ENABLED and REPORT_LAT is not None:
            REPORT_LAT.observe(time.perf_counter() - _start_time)

        # Log after payload retrieval (stage-3)
        logger.info(
            "stage3_event_gen_loaded",
            token=token,
            loaded_flag=payload.get("is_url_input"),
        )

        _cleanup_tasks.pop(token, None)

    # Detach generation from the SSE connection: a background pump drains the
    # generator into a per-token queue so navigating away mid-report can't
    # cancel the LLM work or the final persist — the report still lands in
    # "My Reports". The response merely drains the queue.
    new_job_queue: asyncio.Queue[str | None] = asyncio.Queue()
    _REPORT_JOB_QUEUES[token] = new_job_queue
    _REPORT_JOB_TASKS[token] = asyncio.create_task(
        _pump_report_gen(token, event_generator(), new_job_queue)
    )

    return StreamingResponse(
        _drain_job_queue(new_job_queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
