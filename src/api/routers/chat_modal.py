"""Chat-modal router.

Serves `/chat-modal` which returns fully-hydrated HTML for the paper-chat UI
modal.  The endpoint embeds prior conversation history, hides a paper-ID
input, and rewrites HTMX attributes so the front-end can immediately stream
messages via SSE.
"""

import html
import ipaddress
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
from utils.static_assets import static_url

router: APIRouter = APIRouter()


async def _warm_paper_cache(paper_id: str) -> None:
    """Start full-text extraction + vector indexing for *paper_id*.

    Runs as a response ``BackgroundTask`` when a chat surface opens so the
    first message doesn't wait on the PDF download/extract pipeline.
    """
    if not paper_id or paper_id == "global":
        return
    try:
        paper_service = get_paper_service()
        if paper_service:
            await paper_service.get_paper_content_from_id(paper_id)
    except Exception as exc:  # noqa: BLE001 - prefetch must never break requests
        logger.debug("chat_warm_failed", paper_id=paper_id, error=str(exc))


# ---------------------------------------------------------------------------
# Shared httpx client pool (reuses TCP connections across proxy requests)
# ---------------------------------------------------------------------------
_PROXY_CLIENT: httpx.AsyncClient | None = None


def _get_proxy_client() -> httpx.AsyncClient:
    global _PROXY_CLIENT
    if _PROXY_CLIENT is None or _PROXY_CLIENT.is_closed:
        _PROXY_CLIENT = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _PROXY_CLIENT


# ---------------------------------------------------------------------------
# SSRF protection: only allow proxying to known academic / research domains
# ---------------------------------------------------------------------------
_ALLOWED_PROXY_DOMAINS: set[str] = {
    "arxiv.org",
    "ar5iv.org",
    "ar5iv.labs.arxiv.org",
    "export.arxiv.org",
    "doi.org",
    "dx.doi.org",
    "semanticscholar.org",
    "api.semanticscholar.org",
    "scholar.google.com",
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "biorxiv.org",
    "medrxiv.org",
    "openreview.net",
    "aclanthology.org",
    "proceedings.mlr.press",
    "papers.neurips.cc",
    "dl.acm.org",
    "ieee.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "plos.org",
    "journals.plos.org",
    "oup.com",
    "academic.oup.com",
    "cell.com",
    "ssrn.com",
    "papers.ssrn.com",
    "nber.org",
    "researchgate.net",
    # Exa's publication index returns exa.ai/library/* links as the canonical
    # paper URL; its pages are server-rendered so the proxy renders the paper.
    "exa.ai",
}


def _is_url_allowed(url: str) -> bool:
    """Validate that *url* targets an allowed domain (SSRF protection)."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        host = host.lower().rstrip(".")

        # Block obvious SSRF: loopback, private IPs
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved:
                return False
        except ValueError:
            pass  # Not an IP literal — continue with domain check

        # Check if host matches or is a subdomain of an allowed domain
        for allowed in _ALLOWED_PROXY_DOMAINS:
            if host == allowed or host.endswith("." + allowed):
                return True
        return False
    except Exception:
        return False


def _is_public_http_url(url: str) -> bool:
    """Looser check for the PDF proxy: any public https host.

    The PDF endpoint blindly streams bytes back — it doesn't inject or
    rewrite content — so the SSRF risk is hitting internal services, not
    content injection. Block non-http(s) and private/reserved IPs only.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        except ValueError:
            pass
        # Block obvious internal hostnames
        return not (
            host in ("localhost", "metadata.google.internal")
            or host.endswith((".internal", ".local", ".localhost"))
        )
    except Exception:
        return False


@router.get("/chat-modal", response_class=HTMLResponse)
async def chat_modal(
    request: Request,
    paper_id: str = Query(..., description="ID of the paper to chat about"),
    title: str | None = Query(None, description="(Optional) Paper title to display"),
    paper_url: str | None = Query(None, description="(Optional) Paper URL for link"),
    audience_type: str
    | None = Query(None, description="(Optional) Audience type key from toolbar"),
    model_key: str | None = Query(None, description="(Optional) Selected model key"),
    report_id: str | None = Query(None, description="(Optional) saved report id"),
    topic_id: str | None = Query(None, description="(Optional) topic id"),
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
        # Attempt metadata for nicer header — skip non-paper ids ('topic',
        # 'topic:*') which have no single-paper metadata.
        if paper_service and paper_id != "topic" and not paper_id.startswith("topic:"):
            try:
                paper = await paper_service.get_paper_metadata(paper_id)
            except Exception as exc:  # noqa: BLE001 - best effort
                logger.warning(
                    f"chat_modal: metadata lookup failed for {paper_id}: {exc}"
                )

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
        from services.chat.chat_state import (
            ChatMessage,
            get_chat_state,
        )  # local import avoids heavy dep

        session_id = getattr(request.state, "session_id", "anon")
        # Topic chats share state per topic, falling back to per-report.
        history_key = (
            f"topic:{topic_id}"
            if topic_id
            else (
                f"topic:{report_id}" if paper_id == "topic" and report_id else paper_id
            )
        )
        history = get_chat_state(session_id).get_conversation(history_key)
        if not history:
            # In-memory state is empty (new session/restart) — hydrate from
            # the persisted thread so reopening a chat restores it.
            from services.core.model_prefs import prefs_owner_id

            user_id = prefs_owner_id(request)
            if user_id:
                from services.persistence.database import (
                    database_available,
                    session_scope,
                )
                from services.persistence.repositories import (
                    get_chat_thread_messages,
                )

                if database_available():
                    persisted = []
                    async for db_session in session_scope():
                        rows = await get_chat_thread_messages(
                            db_session,
                            user_id=user_id,
                            report_id=report_id,
                            paper_id=(
                                history_key.split(":", 1)[0]
                                if history_key.startswith("topic:")
                                else paper_id
                            ),
                            topic_id=topic_id,
                        )
                        persisted = [
                            ChatMessage(role=r.role, content=r.content) for r in rows
                        ]
                    for m in persisted:
                        get_chat_state(session_id).append(history_key, m)
                    history = persisted
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
    _open_tag = f"{_search}>"
    _open_repl = (
        f'{_search} data-chat-pid="{html.escape(paper_id)}" style="visibility:hidden">'
    )
    modal_html = modal_html.replace(_open_tag, _open_repl, 1)

    # If we have history, replace the empty hint with conversation bubbles
    if history_html:
        import re as _re

        # history_html is user/LLM content — passing it as the re.sub
        # replacement string treats backslashes (LaTeX \alpha, \cdot, \() as
        # escapes: legal ones silently mangle text, invalid ones raise
        # re.error → the "unexpected server error" on reopen. A lambda
        # replacement inserts it literally.
        modal_html = _re.sub(
            r'(<div[^>]*class="chat-empty-hint"[^>]*>.*?</div>)',
            lambda _m: history_html,
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
    hidden_report = (
        f"<input type='hidden' id='hidden-report-id' name='report_id' value='{html.escape(report_id)}'/>"
        if report_id
        else ""
    )
    hidden_topic = (
        f"<input type='hidden' id='hidden-topic-id' name='topic_id' value='{html.escape(topic_id)}'/>"
        if topic_id
        else ""
    )
    modal_html = modal_html.replace(
        "<textarea",
        hidden_input
        + hidden_aud
        + hidden_model
        + hidden_report
        + hidden_topic
        + "<textarea",
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
        'hx-post="/chat" hx-include="#hidden-paper-id,#hidden-audience-type,#hidden-model-key,#hidden-report-id,#hidden-topic-id"',
    )

    # If we have a URL, make the H2 title a hyperlink in the rendered HTML
    if link_url:
        modal_html = modal_html.replace(
            f">{modal_title}</h2>",
            f'><a href="{html.escape(link_url)}" target="_blank" rel="noopener noreferrer">{modal_title}</a></h2>',
        )

    dur_ms = int((time.perf_counter() - start_ts) * 1000)
    logger.info("chat_modal_ready", paper_id=paper_id, duration_ms=dur_ms)

    return HTMLResponse(
        content=modal_html, background=BackgroundTask(_warm_paper_cache, paper_id)
    )


@router.get("/chat-panel", response_class=HTMLResponse)
async def chat_panel(
    request: Request,
    paper_id: str = Query(..., description="Paper ID"),
    audience_type: str | None = Query(None),
    model_key: str | None = Query(None),
    no_history: bool = Query(
        False, description="When true, omit prior chat and disable transcript cache"
    ),
    report_id: str | None = Query(None, description="(Optional) saved report id"),
) -> HTMLResponse:  # noqa: D401
    """Return *inline* chat fragment (no modal shell) for embedding.

    Designed for the right-hand column of the document viewer. Provides:
    • chat log div with prior history
    • textarea form that streams to `/chat` via HTMX
    """

    # Generate lightweight IDs so multiple panels can coexist in DOM (unlikely)
    chat_log_id = f"chat-log-{uuid4().hex[:8]}"

    # Load prior history (best-effort)
    from services.chat.chat_state import (
        get_chat_state,
    )  # local import avoids heavier top-level dep

    session_id = getattr(request.state, "session_id", "anon")
    chat_state = get_chat_state(session_id)

    # Topic chats share state per report: "topic:{report_id}"
    history_key = (
        f"topic:{report_id}" if paper_id == "topic" and report_id else paper_id
    )

    hist = [] if no_history else chat_state.get_conversation(history_key)

    # If no in-memory history, attempt to load from on-disk cache once.
    if not hist and not no_history:
        try:
            from services.chat.chat_history_cache import ChatHistoryCache as _CHC

            loaded = _CHC.load(session_id, history_key)
            if loaded:
                from services.chat.chat_state import ChatMessage  # local import

                conv = [ChatMessage(**d) for d in loaded]
                for m in conv:
                    chat_state.append(history_key, m)
                hist = conv
        except Exception:  # noqa: BLE001
            pass

    # Last resort for signed-in users: hydrate from the persisted DB thread.
    if not hist and not no_history:
        try:
            from services.core.model_prefs import prefs_owner_id

            user_id = prefs_owner_id(request)
            if user_id:
                from services.persistence.database import (
                    database_available,
                    session_scope,
                )
                from services.persistence.repositories import (
                    get_chat_thread_messages,
                )

                if database_available():
                    thread_paper_id = (
                        history_key.split(":", 1)[0]
                        if history_key.startswith("topic:")
                        else history_key
                    )
                    async for db_session in session_scope():
                        rows = await get_chat_thread_messages(
                            db_session,
                            user_id=user_id,
                            report_id=report_id,
                            paper_id=thread_paper_id,
                        )
                        from services.chat.chat_state import ChatMessage

                        for r in rows:
                            m = ChatMessage(role=r.role, content=r.content)
                            chat_state.append(history_key, m)
                            hist.append(m)
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
        + (
            f"<input type='hidden' name='report_id' value='{html.escape(report_id)}'/>"
            if report_id
            else ""
        )
    )

    panel_html = f"""
<div class=\"chat-panel\" style=\"display:flex;flex-direction:column;height:100%;\">
  <div id=\"{chat_log_id}\" class=\"chat-log-container modal-body\" {('data-chat-pid="' + html.escape(paper_id) + '"') if not no_history else ""} style=\"flex:1 1 auto;overflow-y:auto;\">{history_html}</div>
  <div class=\"oh-snippet-tray\"></div>
  <form hx-post=\"/chat\" hx-target=\"#{chat_log_id}\" hx-swap=\"beforeend\" method=\"POST\" style=\"display:flex;gap:6px;margin-top:6px;\">
    {hidden_inputs}
    <textarea name=\"message\" rows=\"3\" placeholder=\"Ask about this paper…\" style=\"flex:1;border-radius:4px;padding:5px;\"></textarea>
    <button type=\"submit\" class=\"send-btn\">→</button>
  </form>
</div>
"""

    return HTMLResponse(
        content=panel_html, background=BackgroundTask(_warm_paper_cache, paper_id)
    )


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

    # SSRF protection: any public http(s) host is fine for a blind byte
    # stream — papers come from far more domains than the academic
    # allowlist covers, and the old hard-403 made the viewer unusable.
    if not _is_public_http_url(url):
        raise HTTPException(status_code=403, detail="Domain not allowed for proxying")

    # Forward Range / If-Modified-Since so seeking & caching still work
    fwd_headers: dict[str, str] = {}
    for h in ("range", "if-modified-since", "if-none-match"):
        if h in request.headers:
            fwd_headers[h] = request.headers[h]

    client = _get_proxy_client()
    upstream = await client.send(
        client.build_request("GET", url, headers=fwd_headers),
        stream=True,
    )

    # Prepare response headers – copy selective safe ones
    resp_headers = {
        "Content-Type": upstream.headers.get("Content-Type", "application/pdf"),
        "Content-Length": upstream.headers.get("Content-Length", ""),
        "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        "Content-Range": upstream.headers.get("Content-Range", ""),
        "Cache-Control": "public, max-age=86400",
        "Access-Control-Allow-Origin": "*",
    }

    # Close the upstream response when streaming finishes (client is pooled)
    async def _cleanup() -> None:
        await upstream.aclose()

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

    # SSRF protection: only proxy to allowed academic domains
    if not _is_url_allowed(url):
        raise HTTPException(status_code=403, detail="Domain not allowed for proxying")

    client = _get_proxy_client()
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

    # Cleanup handles closing the upstream response (client is pooled).
    async def _cleanup() -> None:
        await upstream.aclose()

    background = BackgroundTask(_cleanup)

    return HTMLResponse(
        content=text,
        media_type=content_type.split(";")[0],
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Frame-Options": "SAMEORIGIN",
            # Remote HTML is rendered in a sandboxed iframe; block scripts at
            # the response layer too so it cannot execute under our origin.
            "Content-Security-Policy": (
                "frame-ancestors 'self'; "
                "frame-src 'self' https: data:; "
                "default-src 'self' https: data: blob:; "
                "style-src 'self' 'unsafe-inline' https:; "
                "font-src 'self' https: data:; "
                "img-src 'self' data: https:; "
                "script-src 'none'; "
                "connect-src 'self' https:;"
            ),
        },
        background=background,
    )


# paper_id is a URL-style identifier (e.g. https://arxiv.org/abs/…) — needs
# :path so multi-segment ids resolve instead of 404ing.
@router.get("/viewer/{paper_id:path}", response_class=HTMLResponse)
async def viewer_modal(
    request: Request,
    paper_id: str,
    url: str | None = None,
    audience_type: str | None = None,
    model_key: str | None = None,
    title: str | None = None,
    report_id: str | None = Query(None),
) -> HTMLResponse:  # noqa: D401
    """Return a two-pane viewer (PDF or HTML) with side-chat.

    • If *url* ends in `.pdf`, we embed with `<embed>` and mark data-viewer=pdf.
    • Otherwise we treat it as a normal webpage and use a strongly sandboxed
      `<iframe>` so proxied remote HTML cannot execute in our origin.
    • When *url* is omitted we fall back to the existing chat-only modal.
    """

    if paper_id == "topic":
        # Topic chat: left pane is a picker + doc viewer across the report's
        # papers; right pane is the topic-scoped chat. Falls back to the
        # chat-only modal when the report's papers can't be resolved.
        topic_html = await _topic_viewer_html(
            request,
            report_id=report_id,
            title=title,
            audience_type=audience_type,
            model_key=model_key,
        )
        if topic_html is not None:
            return HTMLResponse(content=topic_html)
        return await chat_modal(
            request=request,
            paper_id=paper_id,
            title=title,
            paper_url=None,
            audience_type=audience_type,
            model_key=model_key,
            report_id=report_id,
            topic_id=None,
        )

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
            report_id=report_id,
            topic_id=None,
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
    is_pdf = _resolve_pdf_url(url) is not None

    # Reader-mode tabs — the left pane is a swappable slot. Source is the
    # default embed (PDF via pdf.js or proxied HTML); for arXiv papers an
    # ar5iv HTML render is a real reading surface; Text renders the
    # extractor's plain text so paywalled/unrenderable sources still read.
    from services.core.exa_utils import url_utils as _url_utils

    arxiv_id = _url_utils.extract_arxiv_id(url)
    _tab_js = (
        "this.parentNode.querySelectorAll('.viewer-tab')"
        ".forEach(function(b){b.classList.remove('active')});"
        "this.classList.add('active')"
    )
    src_label = "PDF" if is_pdf else "Source"
    tabs = [
        (
            f'<button type="button" class="viewer-tab active" onclick="{_tab_js}"'
            f' hx-get="/doc-pane?url={urllib.parse.quote_plus(url)}"'
            ' hx-target="#doc-pane-slot" hx-swap="innerHTML">'
            f"{src_label}</button>"
        )
    ]
    if arxiv_id:
        ar5iv_url = f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}"
        tabs.append(
            f'<button type="button" class="viewer-tab" onclick="{_tab_js}"'
            f' hx-get="/doc-pane?url={urllib.parse.quote_plus(ar5iv_url)}"'
            ' hx-target="#doc-pane-slot" hx-swap="innerHTML">HTML</button>'
        )
    text_q = (
        f"paper_id={urllib.parse.quote_plus(paper_id)}"
        f"&url={urllib.parse.quote_plus(url)}"
    )
    tabs.append(
        f'<button type="button" class="viewer-tab" onclick="{_tab_js}"'
        f' hx-get="/text-pane?{text_q}"'
        ' hx-target="#doc-pane-slot" hx-swap="innerHTML"'
        ' hx-indicator="#text-loading">Text</button>'
    )
    tabs_html = '<div class="viewer-tabs">' + "".join(tabs) + "</div>"

    # Determine display title – fall back to generic label when no explicit title given.
    display_title = (
        html.escape(title) if title else ("PDF" if is_pdf else "Web") + " Viewer"
    )

    # Build chat-panel URL with optional context parameters
    panel_q = f"/chat-panel?paper_id={html.escape(paper_id)}"
    if audience_type:
        panel_q += f"&audience_type={urllib.parse.quote_plus(audience_type)}"
    if model_key:
        panel_q += f"&model_key={urllib.parse.quote_plus(model_key)}"
    if report_id:
        panel_q += f"&report_id={urllib.parse.quote_plus(report_id)}"

    embed_html = _doc_embed(url)

    modal_html = f"""
<div class=\"modal\" id=\"viewer-{paper_id}\">
  <div class=\"modal-content pdf-viewer\" data-viewer=\"{"pdf" if is_pdf else "html"}\" data-src-url=\"{safe_url}\">
    <div class=\"modal-header\">
      <h2><a href=\"{safe_url}\" target=\"_blank\" rel=\"noopener noreferrer\">{display_title}</a></h2>
      <button class=\"save-paper-btn\" type=\"button\" title=\"Save to library\"
        data-paper-id=\"{html.escape(paper_id)}\"
        data-paper-title=\"{display_title}\"
        data-paper-url=\"{safe_url}\"
        style=\"background:transparent;border:none;cursor:pointer;color:var(--fg);font-size:1rem;margin-right:0.4rem;\">
        <i class=\"fa-regular fa-bookmark\"></i></button>
      <button class=\"modal-close-btn\" onclick=\"htmx.find('#modal-container').innerHTML=''\">×</button>
    </div>
    <div class=\"viewer-grid\">
      <div class=\"doc-pane-wrap\">
        {tabs_html}
        <div id=\"doc-pane-slot\">
          {embed_html}
        </div>
        <div id=\"text-loading\" class=\"htmx-indicator\" style=\"padding:0.4rem;color:#888;\">Extracting full text…</div>
      </div>
      <div class=\"chat-column\" hx-get=\"{panel_q}\" hx-trigger=\"load delay:100ms\" hx-target=\"this\" hx-swap=\"innerHTML\" style=\"min-width:320px; max-width:100%; overflow:hidden;\">
        <div class=\"htmx-indicator\" style=\"padding:1rem; color:#888;\">Loading chat…</div>
      </div>
    </div>
  </div>
</div>
"""
    return HTMLResponse(content=modal_html)


async def _topic_papers(request: Request, report_id: str) -> list[tuple[str, str]]:
    """Return [(url, title)] for the signed-in user's report, title fallback to url."""
    try:
        from services.core.model_prefs import prefs_owner_id

        user_id = prefs_owner_id(request)
        if not user_id:
            return []
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from services.persistence.database import database_available, session_scope
        from services.persistence.models import Report

        if not database_available():
            return []
        async for db_session in session_scope():
            report = await db_session.scalar(
                select(Report)
                .where(Report.id == report_id, Report.user_id == user_id)
                .options(selectinload(Report.papers))
            )
            if not report:
                return []
            return [
                (p.url, p.title or p.url)
                for p in report.papers
                if p.url and p.url.lower().startswith(("http://", "https://"))
            ]
        return []  # pragma: no cover - session_scope yields at least once
    except Exception as exc:  # noqa: BLE001
        logger.debug("topic_papers_lookup_failed", error=str(exc))
        return []


def _resolve_pdf_url(url: str) -> str | None:
    """Return a real PDF URL for *url* when one is derivable, else None.

    Paper URLs are usually landing pages (arXiv abs, DOI resolvers), not
    .pdf links — for arXiv-backed papers the PDF is a deterministic URL.
    """
    if url.lower().endswith(".pdf"):
        return url
    from services.core.exa_utils import url_utils as _url_utils

    arxiv_id = _url_utils.extract_arxiv_id(url)
    if arxiv_id:
        return f"https://arxiv.org/pdf/{arxiv_id}"
    return None


def _doc_embed(url: str) -> str:
    """Viewer-pane markup for a doc URL (pdf.js container or proxied iframe)."""
    pdf_url = _resolve_pdf_url(url)
    if pdf_url:
        proxied = f"/pdf-proxy?url={urllib.parse.quote_plus(pdf_url)}"
        return (
            f'<div class="pdf-container" data-pdf-src="{html.escape(proxied)}"></div>'
            f'<script type="module" src="{static_url("/static/js/pdf-viewer.js")}"></script>'
        )
    proxied = f"/html-proxy?url={urllib.parse.quote_plus(url)}"
    return (
        f'<iframe id="doc-frame" src="{html.escape(proxied)}" '
        'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"></iframe>'
    )


@router.get("/doc-pane", response_class=HTMLResponse)
async def doc_pane(
    request: Request, url: str = Query(..., description="Document URL to render")
) -> HTMLResponse:  # noqa: D401
    """Viewer-pane fragment for the topic picker: pdf container or proxied iframe."""
    # Gate like the pdf-proxy: any public http(s) host may reach the embed —
    # the proxies it delegates to enforce their own rules (pdf-proxy: any
    # public host; html-proxy: academic allowlist).
    if not _is_public_http_url(url):
        raise HTTPException(status_code=403, detail="Domain not allowed for proxying")
    return HTMLResponse(content=_doc_embed(url))


_TEXT_PANE_CAP = 200_000


@router.get("/text-pane", response_class=HTMLResponse)
async def text_pane(
    request: Request,
    paper_id: str = Query(...),
    url: str | None = Query(None),
) -> HTMLResponse:
    """Reader-mode text pane: the extractor's plain text in a clean column.

    Serves papers whose source won't render (paywalled DOIs, html-proxy
    blockers) — extraction runs lazily on first request, then hits cache.
    """
    from services.paper.text_extractor import TextExtractor

    ext = TextExtractor.instance()
    text = await ext.read_full_text(paper_id)
    if text is None and url and _is_url_allowed(url):
        try:
            await ext.ensure_cached(url, paper_id=paper_id)
            text = await ext.read_full_text(paper_id)
        except Exception as exc:  # noqa: BLE001 - degrade to honest message
            logger.warning("text_pane_extract_failed", paper_id=paper_id, err=str(exc))
    if text is None:
        return HTMLResponse(
            "<div class='paper-text-pane'><div class='paper-text-empty'>"
            "Full text isn't available for this paper. The PDF or Source "
            "tab may still work.</div></div>"
        )
    body = html.escape(text[:_TEXT_PANE_CAP])
    return HTMLResponse(
        f"<div class='paper-text-pane'><div class='paper-text-body'>{body}</div></div>"
    )


async def _topic_viewer_html(
    request: Request,
    *,
    report_id: str | None,
    title: str | None,
    audience_type: str | None,
    model_key: str | None,
) -> str | None:
    """Two-pane markup for a topic chat, or None to fall back to chat-only."""
    if not report_id:
        return None
    papers = await _topic_papers(request, report_id)
    if not papers:
        return None

    first_url = html.escape(papers[0][0])
    display_title = html.escape(title) if title else "Topic chat"
    options = "".join(
        f'<option value="{i}" data-url="{html.escape(u)}">{html.escape(t)}</option>'
        for i, (u, t) in enumerate(papers)
    )

    panel_q = (
        f"/chat-panel?paper_id=topic&report_id={urllib.parse.quote_plus(report_id)}"
    )
    if audience_type:
        panel_q += f"&audience_type={urllib.parse.quote_plus(audience_type)}"
    if model_key:
        panel_q += f"&model_key={urllib.parse.quote_plus(model_key)}"

    return f"""
<div class=\"modal\" id=\"viewer-topic\">
  <div class=\"modal-content pdf-viewer\" data-viewer=\"topic\">
    <div class=\"modal-header\">
      <h2>{display_title}</h2>
      <button class=\"modal-close-btn\" onclick=\"htmx.find('#modal-container').innerHTML=''\">×</button>
    </div>
    <div class=\"viewer-grid\">
      <div class=\"doc-pane-wrap\" style=\"display:flex;flex-direction:column;min-width:0;\">
        <select id=\"topic-paper-select\" class=\"topic-paper-select\" style=\"margin:0 0 6px;max-width:100%;\"
          onchange=\"var u=this.options[this.selectedIndex].dataset.url;var l=document.getElementById('topic-doc-link');if(l){{l.href=u;l.textContent=this.options[this.selectedIndex].text;}}htmx.ajax('GET','/doc-pane?url='+encodeURIComponent(u),{{target:'#doc-pane',swap:'innerHTML'}})\">
          {options}
        </select>
        <div style=\"font-size:0.8rem;margin:0 0 6px;\"><a id=\"topic-doc-link\" href=\"{first_url}\" target=\"_blank\" rel=\"noopener noreferrer\" style=\"color:#888;\">{html.escape(papers[0][1])}</a></div>
        <div id=\"doc-pane\" class=\"doc-pane\" style=\"flex:1;min-height:0;\" hx-get=\"/doc-pane?url={urllib.parse.quote_plus(papers[0][0])}\" hx-trigger=\"load\" hx-swap=\"innerHTML\">
          <div class=\"htmx-indicator\" style=\"padding:1rem;color:#888;\">Loading document…</div>
        </div>
      </div>
      <div class=\"chat-column\" hx-get=\"{panel_q}\" hx-trigger=\"load delay:100ms\" hx-target=\"this\" hx-swap=\"innerHTML\" style=\"min-width:320px; max-width:100%; overflow:hidden;\">
        <div class=\"htmx-indicator\" style=\"padding:1rem; color:#888;\">Loading chat…</div>
      </div>
    </div>
  </div>
</div>
"""


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
    <style>html,body{{margin:0;padding:0;}}</style>
  </head>
  <body>
    {modal_html}
  </body>
</html>"""

    return HTMLResponse(content=full_html)
