"""Chat message POST & SSE stream endpoints (user assistant)."""

import asyncio
import html
import pathlib as _pathlib
import time
import urllib.parse as _urlparse
from collections.abc import AsyncIterator
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from api.deps import get_llm_service, get_paper_service, get_session_id, get_settings
from config import Config as AppConfig
from pages.main_page import render_chat_message
from services.chat.chat_runtime import build_prompt, get_semaphore
from services.chat.chat_state import ChatMessage, _chat_states, get_chat_state
from utils.observability import _METRICS_ENABLED
from utils.rate_limit import limiter

try:
    # Only present when metrics enabled
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover - metrics disabled in dev
    CHAT_TOKEN_LAT = None


from loguru import logger as log

import config as _cfg

# Interval between SSE keep-alive pings (seconds)
KEEP_ALIVE_SEC = _cfg.config.CHAT_KEEPALIVE_SEC

# Watermark for when a cached full-text file must be refreshed (7 days).
_PAPER_CACHE_TTL_SEC = 60 * 60 * 24 * 7

router = APIRouter()


# 20 requests per minute per IP, bursts of 5 per second
@limiter.limit("20/minute")
@router.post("/chat")
async def chat_post(
    request: Request,  # noqa: D401 - FastAPI signature style
    session_id: str = Depends(get_session_id),
    message: str = Form(...),
    # Use special sentinel "global" when the assistant is not grounded to a specific paper.
    paper_id: str = Form("global"),
    with_sources: str | None = Form(None),
    audience_type: str = Form("technical"),
    model_key: Optional[str] = Form(None),
    report_id: str | None = Form(None),
) -> Response:
    """Handle a user chat message and return HTML skeleton for streaming reply."""

    chat_state = get_chat_state(session_id)

    # Auto-reset stuck stream_active flag if it's been True longer than the timeout.
    if chat_state.stream_active and chat_state._stream_started_at > 0:
        elapsed = time.time() - chat_state._stream_started_at
        if elapsed > chat_state._STREAM_TIMEOUT_SEC:
            log.warning(
                "chat_stream_stuck_reset",
                session_id=session_id,
                elapsed_sec=round(elapsed, 1),
            )
            chat_state.stream_active = False
            chat_state._stream_started_at = 0.0
            chat_state.busy_retries = 0

    # If a stream is already active, signal busy to frontend and let HTMX retry.
    if chat_state.stream_active:
        chat_state.busy_retries += 1
        if chat_state.busy_retries >= 6:
            chat_state.busy_retries = 0  # reset after escalation
            return Response(
                "Assistant still busy - please try again later.", status_code=429
            )
        busy_banner = (
            '<div id="status-banner" hx-swap-oob="innerHTML:#status-banner">'
            "Assistant busy - retrying…"
            "</div>"
        )
        return HTMLResponse(content=busy_banner, status_code=202)

    # Reset counter on successful accept
    chat_state.busy_retries = 0

    _MAX_MSG_CHARS = 3000  # generous cap; frontend limits to 2400
    clean_msg = message.strip()[:_MAX_MSG_CHARS]
    # Ignore empty/whitespace-only submits - return 204 so HTMX no-ops.
    if not clean_msg:
        return Response(status_code=204)

    chat_state.append(paper_id, ChatMessage(role="user", content=clean_msg))
    from services.auth.session import get_auth_user_id
    from services.persistence.database import database_available, session_scope

    user_id = get_auth_user_id(request)
    if user_id and database_available():
        try:
            from services.persistence.repositories import append_chat_message

            async for db_session in session_scope():
                await append_chat_message(
                    db_session,
                    user_id=user_id,
                    report_id=report_id,
                    paper_id=paper_id,
                    role="user",
                    content=clean_msg,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_persist_user_failed", user_id=user_id, error=str(exc))
    log.info(
        "chat_request", session_id=session_id, paper_id=paper_id, chars=len(clean_msg)
    )

    # Reset ring buffer for new assistant response
    chat_state.reset_stream_buffer()
    chat_state.paper_cache.setdefault(paper_id, {})

    # 1. Render the user's message immediately
    user_message_html = str(render_chat_message(clean_msg, "user"))

    # 2. Create a unique container for the assistant's streaming response (raw HTML)
    assistant_response_id = f"assistant-response-{uuid4()}"
    # Full stream URL (used by JS) includes additional audience/model params.
    safe_paper_id = _urlparse.quote_plus(paper_id)
    stream_url = f"/chat/stream?paper_id={safe_paper_id}&audience_type={_urlparse.quote_plus(audience_type)}"
    if model_key:
        stream_url += f"&model_key={_urlparse.quote_plus(model_key)}"
    if report_id:
        stream_url += f"&report_id={_urlparse.quote_plus(report_id)}"

    # Minimal URL expected by legacy tests for HTMX sse-connect attr – only paper_id.
    legacy_sse_url = f"/chat/stream?paper_id={safe_paper_id}"

    assistant_response_container_html = (
        f'<div id="{assistant_response_id}" class="assistant-message typing" '
        f'data-stream-url="{stream_url}" '
        f'sse-connect="{legacy_sse_url}" '
        f'data-batch-ms="8" '
        f'hx-trigger="load">…</div>'
    )

    full_response_html = user_message_html + assistant_response_container_html
    return HTMLResponse(content=full_response_html)


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    paper_id: str,
    audience_type: str = Query("technical"),
    model_key: Optional[str] = Query(None),
    report_id: str | None = Query(None),
    resume_at: int = Query(0, ge=0, description="Token index to resume from."),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> Response:
    """Server-Sent-Events endpoint that streams the assistant's response."""

    session_id = await get_session_id(request)
    if not session_id:
        return Response("Session not found", status_code=400)

    # Ensure services are available
    llm_service = get_llm_service()
    paper_service = get_paper_service()
    if not llm_service or not paper_service:
        return Response("Services not available", status_code=503)

    chat_state = get_chat_state(session_id)

    if resume_at and chat_state.stream_buffer:
        last_idx = chat_state.stream_buffer[-1][0]
        if resume_at >= last_idx:
            log.info(
                "chat_resume_past_tail",
                session_id=session_id,
                requested=resume_at,
                last=last_idx,
            )
            return Response(status_code=204)

    # Reject any concurrent stream attempt
    if chat_state.stream_active:
        return Response(status_code=204)
    chat_state.stream_active = True
    chat_state._stream_started_at = time.time()

    # Log stream start for easier tracing
    log.info(
        "chat_stream_start",
        session_id=session_id,
        paper_id=paper_id,
        resume_at=resume_at,
    )

    async def event_generator() -> AsyncIterator[str]:
        try:
            completed_successfully = False
            assistant_msg: str = ""
            tok_idx: int = 0

            # If client requests resume and we have tokens buffered, replay them.
            if resume_at > 0 and chat_state.stream_buffer:
                for idx, tok in chat_state.stream_buffer:
                    if idx >= resume_at:
                        safe_tok = html.escape(tok).replace("\n", "<br/>")
                        yield f"event: assistant\ndata: {safe_tok}\n\n"
                log.info(
                    "chat_resume_replay_complete",
                    session_id=session_id,
                    replay_start=resume_at,
                )
                return

            async def _client_gone() -> bool:
                if await request.is_disconnected():
                    log.info("client_disconnect_pre_llm", session_id=session_id)
                    chat_state.stream_active = False
                    return True
                return False

            if await _client_gone():
                yield "event: message_done\ndata: \n\n"
                return

            # Ensure paper full text is cached and fresh once per stream
            def _needs_refresh() -> bool:
                # Global (un-grounded) chat has no associated paper file.
                if paper_id == "global":
                    return False
                if paper_id not in chat_state.paper_cache:
                    return True
                path = chat_state.paper_cache.get(paper_id, {}).get("full_text_path")
                if not path:
                    return True
                try:
                    age = time.time() - _pathlib.Path(path).stat().st_mtime
                    return age > _PAPER_CACHE_TTL_SEC
                except FileNotFoundError:
                    return True

            if _needs_refresh():
                path = await paper_service.get_paper_content_from_id(paper_id)
                if path:
                    chat_state.paper_cache.setdefault(paper_id, {})[
                        "full_text_path"
                    ] = path

            if await _client_gone():
                yield "event: message_done\ndata: \n\n"
                return

            last_tok_t = time.perf_counter()
            while True:
                try:
                    if await request.is_disconnected():
                        print("Client disconnected, closing stream.")
                        break

                    if chat_state.last_role(paper_id) == "user":
                        # Build prompt with requested audience style.
                        # build_prompt returns str or (str, list[str]) when figures are available.
                        prompt_result = await build_prompt(
                            chat_state, paper_id, audience_type
                        )
                        _figure_paths: list[str] = []
                        if isinstance(prompt_result, tuple):
                            prompt, _figure_paths = prompt_result
                        else:
                            prompt = prompt_result

                        async with get_semaphore(session_id, paper_id):
                            # Choose model based on user selection (fallback to default).
                            selected_key = model_key or settings.DEFAULT_MODEL_KEY
                            if selected_key not in settings.AVAILABLE_MODELS:
                                selected_key = settings.DEFAULT_MODEL_KEY
                            model_cfg = settings.AVAILABLE_MODELS[selected_key]

                            # TODO: When _figure_paths is non-empty, use a vision-capable
                            # streaming endpoint to include images in the LLM context.
                            # For now, figure descriptions are included as text in the
                            # prompt context (via RAG retrieval), which provides good
                            # coverage without requiring streaming vision support.
                            stream_iter = await llm_service.generate_completion_stream(
                                provider_name=model_cfg["provider"],
                                model_name=model_cfg["id"],
                                prompt=prompt,
                                max_tokens=_cfg.config.CHAT_MAX_TOKENS,
                                temperature=_cfg.config.CHAT_TEMPERATURE,
                                system_prompt=None,
                            )
                            # Batch tokens to reduce SSE event overhead and smooth streaming
                            _batch_buf: list[str] = []
                            _batch_last_flush = time.perf_counter()
                            _BATCH_INTERVAL = 0.08  # flush every ~80ms

                            _client_disconnected = False

                            async for token in stream_iter:
                                if await request.is_disconnected():
                                    if hasattr(stream_iter, "aclose"):
                                        asyncio.create_task(stream_iter.aclose())
                                    _client_disconnected = True
                                    print(
                                        "Client disconnected mid-stream - cancelling generation."
                                    )
                                    break

                                assistant_msg += token
                                tok_idx += 1
                                chat_state.push_token(token)
                                _batch_buf.append(token)

                                # Flush when batch interval elapsed
                                if (
                                    time.perf_counter() - _batch_last_flush
                                    >= _BATCH_INTERVAL
                                ):
                                    chunk = "".join(_batch_buf)
                                    _batch_buf = []
                                    _batch_last_flush = time.perf_counter()
                                    safe_chunk = html.escape(chunk).replace(
                                        "\n", "<br/>"
                                    )
                                    yield f"event: assistant\ndata: {safe_chunk}\n\n"

                                if _METRICS_ENABLED and CHAT_TOKEN_LAT:
                                    now_t = time.perf_counter()
                                    CHAT_TOKEN_LAT.observe(now_t - last_tok_t)
                                    last_tok_t = now_t

                            # Flush any remaining tokens
                            if _batch_buf:
                                chunk = "".join(_batch_buf)
                                _batch_buf = []
                                safe_chunk = html.escape(chunk).replace("\n", "<br/>")
                                yield f"event: assistant\ndata: {safe_chunk}\n\n"

                            if _client_disconnected:
                                completed_successfully = False
                            else:
                                completed_successfully = True
                                log.info(
                                    "chat_stream_complete",
                                    session_id=session_id,
                                    tokens=len(assistant_msg),
                                    total_tokens=tok_idx,
                                )
                            break

                    await asyncio.sleep(KEEP_ALIVE_SEC)
                    yield ": keep-alive\n\n"
                    log.debug("chat_sse_keepalive", session_id=session_id)

                except asyncio.CancelledError:
                    print("Stream cancelled by client.")
                    completed_successfully = False
                    break
                except Exception:  # noqa: BLE001
                    # Capture full traceback for debugging
                    log.exception(
                        "chat_stream_exception",
                        session_id=session_id,
                        paper_id=paper_id,
                    )
                    error_message = html.escape("Sorry, an error occurred.")
                    yield "event: error\n"
                    yield f"data: {error_message}\n\n"
                    log.error("chat_stream_error_event", session_id=session_id)
                    break
        finally:
            log.info(
                "chat_stream_finalise",
                session_id=session_id,
                tokens=len(assistant_msg),
                completed_successfully=completed_successfully,
                total_tokens=tok_idx,
            )
            if completed_successfully and assistant_msg:
                chat_state.append(
                    paper_id, ChatMessage(role="assistant", content=assistant_msg)
                )
                from services.auth.session import get_auth_user_id
                from services.persistence.database import (
                    database_available,
                    session_scope,
                )

                user_id = get_auth_user_id(request)
                if user_id and database_available():
                    try:
                        from services.persistence.repositories import (
                            append_chat_message,
                        )

                        async for db_session in session_scope():
                            await append_chat_message(
                                db_session,
                                user_id=user_id,
                                report_id=report_id,
                                paper_id=paper_id,
                                role="assistant",
                                content=assistant_msg,
                            )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "chat_persist_assistant_failed",
                            user_id=user_id,
                            error=str(exc),
                        )
            else:
                chat_state.reset_stream_buffer()

            chat_state.stream_active = False
            chat_state._stream_started_at = 0.0
            try:
                log.info(
                    "chat_sse_emit_done",
                    session_id=session_id,
                    chars=len(assistant_msg),
                )
                # Legacy compatibility events expected by tests/frontend
                yield "event: done\ndata: \n\n"
                yield "event: message_done\ndata: \n\n"
                yield "event: sse-close\ndata: \n\n"
            except Exception:  # pragma: no cover - client already gone.
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Abort endpoint - invoked by frontend when user closes the chat modal mid-stream.
# It simply flips the `stream_active` flag so subsequent /chat requests are not
# rejected. Full LLM cancellation is cooperative via check in event generator.
# ---------------------------------------------------------------------------
@router.post("/chat/abort")
async def chat_abort(
    request: Request,
    paper_id: str = Form(...),
    session_id: str = Depends(get_session_id),
) -> Response:  # noqa: D401 - FastAPI style
    """Abort any active stream for the given paper in this session."""

    chat_state = get_chat_state(session_id)
    if chat_state.stream_active:
        chat_state.stream_active = False
        log.info("chat_abort", session_id=session_id, paper_id=paper_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Clear-session endpoint - invoked via navigator.sendBeacon on page unload to
# wipe the in-memory ChatState so chats do not persist across hard refreshes or
# browser restarts. Only affects the current session ID.
# ---------------------------------------------------------------------------
@router.post("/chat/clear_session")
async def chat_clear_session(
    session_id: str = Depends(get_session_id),
) -> Response:
    """Drop the entire ChatState for the current session.

    The frontend calls this via `navigator.sendBeacon` in a `beforeunload` handler.
    If the user has multiple tabs open we only clear the state when the last tab
    is closed, which is acceptable for the intended UX (per-tab report play
    session).
    """

    try:
        if session_id in _chat_states:
            del _chat_states[session_id]
    except Exception:
        log.warning("chat_clear_session_memory_failed", session_id=session_id)

    # Also remove disk-persisted session state so it doesn't get reloaded.
    try:
        from services.chat.session_state_cache import SessionStateCache

        SessionStateCache.delete(session_id)
    except Exception:  # noqa: BLE001
        log.warning("chat_clear_session_disk_failed", session_id=session_id)

    return Response(status_code=204)
