"""Chat-modal router.

Serves `/chat-modal` which returns fully-hydrated HTML for the paper-chat UI
modal.  The endpoint embeds prior conversation history, hides a paper-ID
input, and rewrites HTMX attributes so the front-end can immediately stream
messages via SSE.
"""

import html
import time
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_paper_service  # lightweight helper, no heavy imports
from services.chat.chat_components import ChatBubble, ChatModal

router: APIRouter = APIRouter()


@router.get("/chat-modal", response_class=HTMLResponse)
async def chat_modal(
    request: Request,
    paper_id: str = Query(..., description="ID of the paper to chat about"),
    title: str | None = Query(None, description="(Optional) Paper title to display"),
    paper_url: str | None = Query(None, description="(Optional) Paper URL for link"),
    audience_type: str | None = Query(
        None, description="(Optional) Audience type key from toolbar"
    ),
    model_key: str | None = Query(None, description="(Optional) Selected model key"),
) -> HTMLResponse:  # noqa: D401 - pydantic description fine
    """Return a fully-formed chat modal for *paper_id*.

    The modal contains a chat log container, a textarea form that posts to `/chat`,
    and is pre-wired for SSE streaming from `/chat/stream`.
    """

    start_ts = time.perf_counter()

    # Best-effort metadata fetch; failures fall back to bare modal.
    try:
        paper_service = get_paper_service()
    except Exception:  # noqa: BLE001 – avoid hard failure on optional metadata
        paper_service = None

    paper = None
    if paper_service:  # Attempt to fetch metadata for nicer header
        try:
            paper = await paper_service.get_paper_metadata(paper_id)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning(f"chat_modal: metadata lookup failed for {paper_id}: {exc}")

    # Default title if no metadata is found
    modal_title = paper_id

    if title:
        modal_title = html.escape(title)

    provided_url = paper_url

    link_url: str | None = None

    # If caller didn't provide title, try service lookup
    if not title and paper and (svc_title := getattr(paper, "title", None)):
        link_url = getattr(paper, "url", None)
        modal_title = html.escape(svc_title)

    # Determine final link_url precedence: caller > service > None
    if provided_url:
        link_url = provided_url

    modal_id = f"modal-{uuid4().hex[:8]}"
    # Use short random id for HTML-safe selector; paperId may contain / and :
    chat_log_id = f"chat-log-{uuid4().hex[:8]}"

    # ----------------- Retrieve prior conversation (if any) -----------------
    try:
        from services.chat.chat_state import get_chat_state  # local import avoids heavy dep

        session_id = getattr(request.state, "session_id", "anon")
        history = get_chat_state(session_id).get_conversation(paper_id)
    except Exception:  # noqa: BLE001 - fallback silently
        history = []

    history_html = "".join(str(ChatBubble(msg.content, msg.role)) for msg in history)

    # Build modal component
    modal_html = str(
        ChatModal(
            modal_id=modal_id,
            title=modal_title,
            chat_log_id=chat_log_id,
            audience_type=audience_type,
        )
    )

    # Original snippet in ChatModal markup (no hx-on).
    # Note: ChatModal adds both "chat-log-container" and "modal-body" classes
    # (in that order). Match the exact string so string.replace succeeds.
    _search = f'id="{chat_log_id}" class="chat-log-container modal-body"'

    chat_log_repl = (
        f'{_search} data-chat-pid="{html.escape(paper_id)}" style="visibility:hidden"'
        + (f">{history_html}" if history_html else ">")
    )

    modal_html = modal_html.replace(f"{_search}></div>", f"{chat_log_repl}</div>")

    # Inject hidden paper_id input and fix HX attribute names for HTMX
    hidden_input = f"<input type='hidden' id='hidden-paper-id' name='paper_id' value='{paper_id}'/>"
    hidden_aud = (
        f"<input type='hidden' id='hidden-audience-type' name='audience_type' value='{html.escape(audience_type)}'/>"
        if audience_type
        else ""
    )
    hidden_model = (
        f"<input type='hidden' id='hidden-model-key' name='model_key' value='{html.escape(model_key)}'/>"
        if model_key
        else ""
    )
    modal_html = modal_html.replace(
        "<textarea", hidden_input + hidden_aud + hidden_model + "<textarea"
    )

    # Replace underscored HX attributes with hyphenated versions expected by HTMX
    modal_html = (
        modal_html.replace("hx_post=", "hx-post=")
        .replace("hx_target=", "hx-target=")
        .replace("hx_swap=", "hx-swap=")
        .replace("hx_on_click=", "hx-on:click=")
        .replace("hx_on=", "hx-on=")
    )
    # Add hx-include for hidden paper field on the chat form
    modal_html = modal_html.replace(
        'hx-post="/chat"',
        'hx-post="/chat" hx-include="#hidden-paper-id,#hidden-audience-type,#hidden-model-key"',
    )

    # If we have a URL, make the H2 title a hyperlink in the rendered HTML
    if link_url:
        modal_html = modal_html.replace(
            f"<h2>{modal_title}</h2>",
            f'<h2><a href="{html.escape(link_url)}" target="_blank" rel="noopener noreferrer">{modal_title}</a></h2>',
        )

    dur_ms = int((time.perf_counter() - start_ts) * 1000)
    logger.info("chat_modal_ready", paper_id=paper_id, duration_ms=dur_ms)

    return HTMLResponse(content=modal_html)
