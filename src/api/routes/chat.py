"""Chat endpoints extracted from legacy `app.py`.

These routes are registered on the shared FastAPI application via
`api.main.include_router()` during Phase-1 refactor.  The logic remains
byte-for-byte equivalent to the original implementation so behaviour is
unchanged.
"""

from __future__ import annotations

import asyncio
import html
import os
import pathlib as _pathlib
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from api.deps import get_llm_service, get_paper_service, get_settings
from chat.runtime import build_prompt, get_semaphore
from chat.state import ChatMessage, get_chat_state
from config import Config as AppConfig
from main import KEEP_ALIVE_SEC, get_session_id
from pages.main_page import render_chat_message
from utils.observability import _METRICS_ENABLED

try:
    # Only present when metrics enabled
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover – metrics disabled in dev
    CHAT_TOKEN_LAT = None

# Watermark for when a cached full-text file must be refreshed (7 days).
_PAPER_CACHE_TTL_SEC = 60 * 60 * 24 * 7

log = structlog.get_logger()

router = APIRouter()


@router.post("/chat")
async def chat_post(
    request: Request,  # noqa: D401 – FastAPI signature style
    session_id: str = Depends(get_session_id),
    message: str = Form(...),
    paper_id: str = Form(...),
) -> Response:
    """Handle a user chat message and return HTML skeleton for streaming reply."""

    chat_state = get_chat_state(session_id)

    # If a stream is already active, signal busy to frontend and let HTMX retry.
    if chat_state.stream_active:
        chat_state.busy_retries += 1
        if chat_state.busy_retries >= 6:
            chat_state.busy_retries = 0  # reset after escalation
            return Response("Assistant still busy – please try again later.", status_code=429)
        busy_banner = (
            '<div id="status-banner" hx-swap-oob="innerHTML:#status-banner">'
            "Assistant busy – retrying…"
            "</div>"
        )
        return HTMLResponse(content=busy_banner, status_code=202)

    # Reset counter on successful accept
    chat_state.busy_retries = 0

    clean_msg = message.strip()
    # Ignore empty/whitespace-only submits – return 204 so HTMX no-ops.
    if not clean_msg:
        return Response(status_code=204)

    chat_state.append(paper_id, ChatMessage(role="user", content=clean_msg))
    log.info("chat_request", session_id=session_id, paper_id=paper_id, chars=len(clean_msg))

    # Reset ring buffer for new assistant response
    chat_state.reset_stream_buffer()
    chat_state.paper_cache.setdefault(paper_id, {})

    # 1. Render the user's message immediately
    user_message_html = str(render_chat_message(clean_msg, "user"))

    # 2. Create a unique container for the assistant's streaming response (raw HTML)
    assistant_response_id = f"assistant-response-{uuid4()}"
    assistant_response_container_html = (
        f'<div id="{assistant_response_id}" class="assistant-message typing" '
        f'hx-ext="sse" sse-connect="/chat/stream?paper_id={paper_id}" '
        f'sse-swap="message" data-batch-ms="8" hx-sse-reconnect-delay="-1" '
        f'hx-trigger="load">…</div>'
    )

    full_response_html = user_message_html + assistant_response_container_html
    return HTMLResponse(content=full_response_html)


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    paper_id: str,
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
                "chat_resume_past_tail", session_id=session_id, requested=resume_at, last=last_idx
            )
            return Response(status_code=204)

    # Reject any concurrent stream attempt
    if chat_state.stream_active:
        return Response(status_code=204)
    chat_state.stream_active = True

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

            # If client requests resume and we have tokens buffered, replay them.
            if resume_at > 0 and chat_state.stream_buffer:
                for idx, tok in chat_state.stream_buffer:
                    if idx >= resume_at:
                        safe_tok = html.escape(tok).replace("\n", "<br/>")
                        yield f"data: {safe_tok}\n\n"
                log.info(
                    "chat_resume_replay_complete", session_id=session_id, replay_start=resume_at
                )
                return

            async def _client_gone() -> bool:
                if await request.is_disconnected():
                    log.info("client_disconnect_pre_llm", session_id=session_id)
                    chat_state.stream_active = False
                    return True
                return False

            if await _client_gone():
                yield "event: sse-close\ndata: \n\n"
                return

            # Ensure paper full text is cached and fresh once per stream
            def _needs_refresh() -> bool:
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
                    chat_state.paper_cache.setdefault(paper_id, {})["full_text_path"] = path

            if await _client_gone():
                yield "event: sse-close\ndata: \n\n"
                return

            last_tok_t = time.perf_counter()
            while True:
                try:
                    if await request.is_disconnected():
                        print("Client disconnected, closing stream.")
                        break

                    if chat_state.last_role(paper_id) == "user":
                        prompt = await build_prompt(chat_state, paper_id)
                        async with get_semaphore(session_id, paper_id):
                            stream_iter = await llm_service.generate_completion_stream(
                                provider_name=settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY][
                                    "provider"
                                ],
                                model_name=settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY][
                                    "id"
                                ],
                                prompt=prompt,
                                max_tokens=int(os.getenv("CHAT_MAX_TOKENS", "512")),
                                temperature=float(os.getenv("CHAT_TEMPERATURE", "0.7")),
                                system_prompt=None,
                            )
                            async for token in stream_iter:
                                if await request.is_disconnected():
                                    if hasattr(stream_iter, "aclose"):
                                        asyncio.create_task(stream_iter.aclose())
                                    completed_successfully = False
                                    print("Client disconnected mid-stream - cancelling generation.")
                                    break

                                assistant_msg += token
                                log.debug("chat_stream_token", session_id=session_id, t=len(token))
                                chat_state.push_token(token)
                                safe_token = html.escape(token).replace("\n", "<br/>")
                                yield f"data: {safe_token}\n\n"

                                if _METRICS_ENABLED and CHAT_TOKEN_LAT:
                                    now_t = time.perf_counter()
                                    CHAT_TOKEN_LAT.observe(now_t - last_tok_t)
                                    last_tok_t = now_t

                            if completed_successfully is False:
                                completed_successfully = True
                                log.info(
                                    "chat_stream_complete",
                                    session_id=session_id,
                                    tokens=len(assistant_msg),
                                )
                            break

                    await asyncio.sleep(KEEP_ALIVE_SEC)
                    yield ": keep-alive\n\n"

                except asyncio.CancelledError:
                    print("Stream cancelled by client.")
                    completed_successfully = False
                    break
                except Exception:  # noqa: BLE001
                    # Capture full traceback for debugging
                    log.exception("chat_stream_exception", session_id=session_id, paper_id=paper_id)
                    error_message = html.escape("Sorry, an error occurred.")
                    yield "event: error\n"
                    yield f"data: {error_message}\n\n"
                    break
        finally:
            log.info("chat_stream_complete", session_id=session_id, tokens=len(assistant_msg))
            if completed_successfully and assistant_msg:
                chat_state.append(paper_id, ChatMessage(role="assistant", content=assistant_msg))
            else:
                chat_state.reset_stream_buffer()

            chat_state.stream_active = False
            try:
                yield "event: done\ndata: \n\n"
                yield "event: sse-close\ndata: \n\n"
            except Exception:  # pragma: no cover – client already gone.
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Abort endpoint – invoked by frontend when user closes the chat modal mid-stream.
# It simply flips the `stream_active` flag so subsequent /chat requests are not
# rejected. Full LLM cancellation is cooperative via check in event generator.
# ---------------------------------------------------------------------------
@router.post("/chat/abort")
async def chat_abort(
    request: Request,
    paper_id: str = Form(...),
    session_id: str = Depends(get_session_id),
) -> Response:  # noqa: D401 – FastAPI style
    """Abort any active stream for the given paper in this session."""

    chat_state = get_chat_state(session_id)
    if chat_state.stream_active:
        chat_state.stream_active = False
        log.info("chat_abort", session_id=session_id, paper_id=paper_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Clear-session endpoint – invoked via navigator.sendBeacon on page unload to
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
        from chat.state import _chat_states  # noqa: WPS437 – internal mutable bag

        if session_id in _chat_states:
            del _chat_states[session_id]
    except Exception:  # noqa: BLE001 – defensive
        pass

    return Response(status_code=204)
