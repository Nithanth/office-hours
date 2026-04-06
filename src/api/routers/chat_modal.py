"""Chat-modal router.

Serves `/chat-modal` which returns fully-hydrated HTML for the paper-chat UI
modal.  The endpoint embeds prior conversation history, hides a paper-ID
input, and rewrites HTMX attributes so the front-end can immediately stream
messages via SSE.
"""

import asyncio
import html
import re
import time
import urllib.parse
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger
from starlette.background import BackgroundTask

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

    # Single declaration to satisfy type checker; will be updated later.
    link_url: str | None = None

    # Special un-grounded assistant modal
    if paper_id == "global":
        modal_title = "Research Assistant"
    else:
        if paper_service:  # Attempt to fetch metadata for nicer header
            try:
                paper = await paper_service.get_paper_metadata(paper_id)
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(f"chat_modal: metadata lookup failed for {paper_id}: {exc}")

    # Default title if no metadata is found
    if paper_id != "global":
        modal_title = paper_id

    if title:
        modal_title = html.escape(title)

    provided_url = paper_url

    # If caller didn't provide title, try service lookup
    if not title and paper and (svc_title := getattr(paper, "title", None)):
        link_url = getattr(paper, "url", None)
        modal_title = html.escape(svc_title)

    # Determine final link_url precedence: caller > service > None
    if provided_url:
        link_url = provided_url

    # ------------------------------------------------------------------
    # If caller supplied a direct PDF link, delegate to the two-pane viewer
    # so the document and chat appear side-by-side. Non-PDFs keep the
    # original single-modal flow.
    # ------------------------------------------------------------------
    if link_url and link_url.lower().endswith(".pdf"):
        return await viewer_modal(
            request=request,
            paper_id=paper_id,
            url=link_url,
            audience_type=audience_type,
            model_key=model_key,
            title=modal_title,
        )

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
            paper_id=paper_id,
        )
    )

    # Inject data-chat-pid and replace the empty-hint placeholder with
    # prior history when available.  The chat-log div rendered by ChatModal
    # contains a .chat-empty-hint child; we swap the entire inner content.
    _search = f'id="{chat_log_id}" class="chat-log-container modal-body"'

    # Find the chat-log div and replace its contents
    _open_tag = f'{_search}>'
    _open_repl = f'{_search} data-chat-pid="{html.escape(paper_id)}" style="visibility:hidden">'
    modal_html = modal_html.replace(_open_tag, _open_repl, 1)

    # If we have history, replace the empty hint with conversation bubbles
    if history_html:
        import re as _re
        modal_html = _re.sub(
            r'(<div[^>]*class="chat-empty-hint"[^>]*>.*?</div>)',
            history_html,
            modal_html,
            count=1,
        )

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
            f">{modal_title}</h2>",
            f'><a href="{html.escape(link_url)}" target="_blank" rel="noopener noreferrer">{modal_title}</a></h2>',
        )

    dur_ms = int((time.perf_counter() - start_ts) * 1000)
    logger.info("chat_modal_ready", paper_id=paper_id, duration_ms=dur_ms)

    return HTMLResponse(content=modal_html)


@router.get("/chat-panel", response_class=HTMLResponse)
async def chat_panel(
    request: Request,
    paper_id: str = Query(..., description="Paper ID"),
    audience_type: str | None = Query(None),
    model_key: str | None = Query(None),
    no_history: bool = Query(
        False, description="When true, omit prior chat and disable transcript cache"
    ),
) -> HTMLResponse:  # noqa: D401
    """Return *inline* chat fragment (no modal shell) for embedding.

    Designed for the right-hand column of the document viewer. Provides:
    • chat log div with prior history
    • textarea form that streams to `/chat` via HTMX
    """

    # Generate lightweight IDs so multiple panels can coexist in DOM (unlikely)
    chat_log_id = f"chat-log-{uuid4().hex[:8]}"

    # Load prior history (best-effort)
    from services.chat.chat_state import get_chat_state  # local import avoids heavier top-level dep

    session_id = getattr(request.state, "session_id", "anon")
    chat_state = get_chat_state(session_id)

    hist = [] if no_history else chat_state.get_conversation(paper_id)

    # If no in-memory history, attempt to load from on-disk cache once.
    if not hist and not no_history:
        try:
            from services.chat.chat_history_cache import ChatHistoryCache as _CHC

            loaded = _CHC.load(paper_id)
            if loaded:
                from services.chat.chat_state import ChatMessage  # local import

                conv = [ChatMessage(**d) for d in loaded]
                for m in conv:
                    chat_state.append(paper_id, m)
                hist = conv
        except Exception:  # noqa: BLE001
            pass

    history_html = "".join(str(ChatBubble(m.content, m.role)) for m in hist)

    hidden_inputs = (
        f"<input type='hidden' name='paper_id' value='{html.escape(paper_id)}'/>"
        + (
            f"<input type='hidden' name='audience_type' value='{html.escape(audience_type)}'/>"
            if audience_type
            else ""
        )
        + (
            f"<input type='hidden' name='model_key' value='{html.escape(model_key)}'/>"
            if model_key
            else ""
        )
    )

    panel_html = f"""
<div class=\"chat-panel\" style=\"display:flex;flex-direction:column;height:100%;\">
  <div id=\"{chat_log_id}\" class=\"chat-log-container modal-body\" {('data-chat-pid="' + html.escape(paper_id) + '"') if not no_history else ""} style=\"flex:1 1 auto;overflow-y:auto;\">{history_html}</div>
  <form hx-post=\"/chat\" hx-target=\"#{chat_log_id}\" hx-swap=\"beforeend\" method=\"POST\" style=\"display:flex;gap:6px;margin-top:6px;\">
    {hidden_inputs}
    <textarea name=\"message\" rows=\"3\" placeholder=\"Ask about this paper…\" style=\"flex:1;border-radius:4px;padding:5px;\"></textarea>
    <button type=\"submit\" class=\"send-btn\">→</button>
  </form>
</div>
"""

    return HTMLResponse(content=panel_html)


# ---------------------------------------------------------------------------
# PDF proxy: streams a remote PDF so it is same-origin for pdf.js, bypassing CORS
# ---------------------------------------------------------------------------
@router.get("/pdf-proxy", response_class=StreamingResponse)
async def pdf_proxy(
    request: Request, url: str = Query(..., description="Remote PDF URL")
) -> StreamingResponse:  # noqa: D401
    """Stream *url* so the browser treats the PDF as same-origin.

    Supports Range requests so pdf.js can fetch partial chunks. Adds
    `Access-Control-Allow-Origin: *` so client-side canvas rendering works.
    """

    # Basic validation – only allow http(s)
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    # Forward Range / If-Modified-Since so seeking & caching still work
    fwd_headers: dict[str, str] = {}
    for h in ("range", "if-modified-since", "if-none-match"):
        if h in request.headers:
            fwd_headers[h] = request.headers[h]

    client = httpx.AsyncClient(timeout=30)
    upstream = await client.get(url, headers=fwd_headers, follow_redirects=True)

    # Prepare response headers – copy selective safe ones
    resp_headers = {
        "Content-Type": upstream.headers.get("Content-Type", "application/pdf"),
        "Content-Length": upstream.headers.get("Content-Length", ""),
        "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        "Content-Range": upstream.headers.get("Content-Range", ""),
        "Cache-Control": "public, max-age=86400",
        "Access-Control-Allow-Origin": "*",
    }

    # Close upstream and HTTP client when streaming finishes
    async def _cleanup() -> None:
        await upstream.aclose()
        await client.aclose()

    background = BackgroundTask(_cleanup)

    return StreamingResponse(
        upstream.aiter_bytes(),
        status_code=upstream.status_code,
        headers=resp_headers,
        background=background,
    )


# ---------------------------------------------------------------------------
# HTML proxy: fetches remote pages, strips XFO/CSP and injects <base> so they
# can be embedded side-by-side with chat.
# ---------------------------------------------------------------------------
@router.get("/html-proxy", response_class=HTMLResponse)
async def html_proxy(
    request: Request, url: str = Query(..., description="Remote HTML URL")
) -> HTMLResponse:  # noqa: D401
    """Return *url*'s contents with frame-blocking headers removed.

    We download the page server-side, inject a <base> element so relative links
    resolve, and stream it back from our own origin - making it iframe-able in
    the viewer.
    """

    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL scheme")

    client = httpx.AsyncClient(timeout=30, follow_redirects=True)
    upstream = await client.get(url)

    # Decode using httpx' detected encoding.
    text: str = upstream.text
    content_type = upstream.headers.get("Content-Type", "text/html")

    # Remove frame-blocking meta CSP/XFO tags to allow embedding
    text = re.sub(
        r"<meta[^>]+http-equiv=[\"']?(?:content-security-policy|x-frame-options)[\"']?[^>]*>",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Compute base href for relative resources.
    try:
        parsed = urllib.parse.urlparse(str(upstream.url))
        base_href = f"{parsed.scheme}://{parsed.netloc}/"

        if "<head" in text.lower() and "<base" not in text.lower():
            # Inject right after opening <head> tag.
            text = re.sub(
                r"(<head[^>]*>)",
                r"\1<base href=\"" + html.escape(base_href) + '">',
                text,
                count=1,
                flags=re.IGNORECASE,
            )
    except Exception:  # noqa: BLE001 – best-effort only
        pass

    # Cleanup handles closing the upstream response & HTTP client after send.
    async def _cleanup() -> None:
        await upstream.aclose()
        await client.aclose()

    background = BackgroundTask(_cleanup)

    return HTMLResponse(
        content=text,
        media_type=content_type.split(";")[0],
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Frame-Options": "SAMEORIGIN",
            # Allow the proxied document to fetch its own CSS/JS/fonts/images over HTTPS
            "Content-Security-Policy": (
                "frame-ancestors 'self'; "
                "frame-src 'self' https: data:; "
                "default-src 'self' https: data: blob:; "
                "style-src 'self' 'unsafe-inline' https:; "
                "font-src 'self' https: data:; "
                "img-src 'self' data: https:; "
                "script-src 'self' 'unsafe-inline' https:; "
                "connect-src 'self' https:;"
            ),
        },
        background=background,
    )


@router.get("/viewer/{paper_id}", response_class=HTMLResponse)
async def viewer_modal(
    request: Request,
    paper_id: str,
    url: str | None = None,
    audience_type: str | None = None,
    model_key: str | None = None,
    title: str | None = None,
) -> HTMLResponse:  # noqa: D401
    """Return a two-pane viewer (PDF or HTML) with side-chat.

    • If *url* ends in `.pdf`, we embed with `<embed>` and mark data-viewer=pdf.
    • Otherwise we treat it as a normal webpage and use an `<iframe>` with
      sandboxing disabled for now (iframe navigation stays inside the pane).
    • When *url* is omitted we fall back to the existing chat-only modal.
    """

    if not url:
        # Fallback to chat-only modal delivered via its own endpoint.
        # Must pass explicit None values so we don't forward FastAPI's Query() objects.
        return await chat_modal(
            request=request,
            paper_id=paper_id,
            title=None,
            paper_url=None,
            audience_type=None,
            model_key=None,
        )

    # --- Canonicalise arXiv/Ar5iv URLs so missing .pdf suffixes or ar5iv stubs still render ---
    canonical_url = url
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if host.endswith("arxiv.org"):
            if parsed.path.startswith("/pdf/") and not parsed.path.endswith(".pdf"):
                canonical_url = url + ".pdf"
        elif host.endswith("ar5iv.org") or host.endswith("ar5iv.labs.arxiv.org"):
            m = re.match(r"/pdf/([^/?#]+)", parsed.path)
            if m:
                canonical_url = f"https://arxiv.org/pdf/{m.group(1)}.pdf"
    except Exception:  # noqa: BLE001 – best-effort only
        pass

    url = canonical_url

    safe_url = html.escape(url)
    is_pdf = safe_url.lower().endswith(".pdf")

    # Determine display title – fall back to generic label when no explicit title given.
    display_title = html.escape(title) if title else ("PDF" if is_pdf else "Web") + " Viewer"

    # Build chat-panel URL with optional context parameters
    panel_q = f"/chat-panel?paper_id={html.escape(paper_id)}"
    if audience_type:
        panel_q += f"&audience_type={urllib.parse.quote_plus(audience_type)}"
    if model_key:
        panel_q += f"&model_key={urllib.parse.quote_plus(model_key)}"

    # Build viewer pane markup. PDFs go through our proxy so they render via pdf.js.
    if is_pdf:
        proxied = f"/pdf-proxy?url={urllib.parse.quote_plus(url)}"
        embed_html = (
            f'<div class="pdf-container" data-pdf-src="{proxied}"></div>'
            f'<script type="module" src="/static/js/pdf-viewer.js"></script>'
        )
    else:
        proxied = f"/html-proxy?url={urllib.parse.quote_plus(url)}"
        embed_html = f'<iframe id="doc-frame" src="{proxied}" sandbox="allow-same-origin allow-scripts"></iframe>'

    modal_html = f"""
<div class=\"modal\" id=\"viewer-{paper_id}\">
  <div class=\"modal-content pdf-viewer\" data-viewer=\"{"pdf" if is_pdf else "html"}\" data-src-url=\"{safe_url}\">
    <div class=\"modal-header\">
      <h2><a href=\"{safe_url}\" target=\"_blank\" rel=\"noopener noreferrer\">{display_title}</a></h2>
      <button class=\"modal-close-btn\" onclick=\"htmx.find('#modal-container').innerHTML=''\">×</button>
    </div>
    <div class=\"viewer-grid\">
      {embed_html}
      <div class=\"chat-column\" hx-get=\"{panel_q}\" hx-trigger=\"load delay:100ms\" hx-target=\"this\" hx-swap=\"innerHTML\" style=\"min-width:320px; max-width:100%; overflow:hidden;\">
        <div class=\"htmx-indicator\" style=\"padding:1rem; color:#888;\">Loading chat…</div>
      </div>
    </div>
  </div>
</div>
"""
    return HTMLResponse(content=modal_html)


@router.get("/viewer-page/{paper_id}", response_class=HTMLResponse)
async def viewer_page(
    request: Request,
    paper_id: str,
    url: str = Query(..., description="Document URL (pdf/html)"),
) -> HTMLResponse:
    """Standalone **full** HTML page that renders the viewer+chat immediately.

    This avoids any dependency on HTMX - handy for quick debugging or when the
    CDN is blocked. It simply reuses the existing `viewer_modal` handler to
    construct the split-view markup, then inserts that into a minimal page
    shell that loads the global CSS.
    """

    # Re-use the existing modal generator so we keep behaviour in one place.
    modal_resp: HTMLResponse = await viewer_modal(request, paper_id, url)
    modal_html: str = bytes(modal_resp.body).decode()

    # Basic shell. We *do* need HTMX so the right-hand chat panel loads via
    # `hx-get` once the page finishes rendering.
    full_html = f"""<!DOCTYPE html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\"/>
    <title>Viewer test</title>
    <link rel=\"stylesheet\" href=\"/static/css/modal.css\"/>
    <link rel=\"stylesheet\" href=\"/static/css/forms.css\"/>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <!-- Minimal chat stack: SSE pool & stream logic -->
    <script src="/static/js/htmx-sse-pool.js"></script>
    <script type="module" src="/static/js/chat/stream.js"></script>
    <style>html,body{{margin:0;padding:0;font-family:system-ui,sans-serif;}}</style>
  </head>
  <body>
    {modal_html}
  </body>
</html>"""

    return HTMLResponse(content=full_html)
