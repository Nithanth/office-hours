"""Study-notes, blog-post, and Notion export generators.

POST `/generate-report-notes` – downloadable notes (`.txt`, `.md`, or `.docx`).
POST `/generate-blog` – queues a blog generation, returns a view URL immediately.
GET  `/blog-status` – polled by the blog page to fetch markdown when ready.
GET  `/blog-view` – standalone rendered blog page.
POST `/export-notion` – push report to a Notion page/database and return its URL.
"""

import asyncio
import html as _html
import time as _time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from api.deps import get_llm_service, get_session_id
from config import config
from prompts.prompt_builder import _load_template
from services.chat.chat_state import ChatState, get_chat_state
from services.core.llm_service import LLMService
from services.notes.notes_generator import NotesGenerator
from utils.dedupe import deduplicate_chat
from utils.rate_limit import limiter

router = APIRouter(tags=["report-notes"])


def _gather_chat_digest(cs: ChatState, char_cap: int = 5000) -> str:
    """Return a plain-text digest of the recent chat messages (all papers).

    The digest is truncated to *char_cap* to keep prompt size reasonable.
    """

    parts: list[str] = []
    for pid, turns in cs._histories.items():
        if not turns:
            continue
        parts.append(f"### Discussion about {pid}")
        for t in turns:
            role = "User" if t.role == "user" else "Assistant"
            parts.append(f"• {role}: {t.content}")

    digest = "\n".join(parts)
    return digest[:char_cap]


# paper_id optional – helps fetch RAG snippets for extra key takeaways
@limiter.limit("20/5minute")
@router.post("/generate-report-notes")
async def generate_report_notes(
    request: Request,
    report_text: str = Form(...),
    output_format: Literal["txt", "md", "docx"] = Form("txt"),
    paper_id: str | None = Form(None),  # deprecated, kept for backward compat
) -> Response:
    """Generate study notes from *report_text* and recent chat history.

    The notes are returned as a downloadable `.txt` or `.docx` attachment.
    """

    logger.info("report_notes: request received", output_format=output_format)

    if not report_text.strip():
        logger.warning("report_notes: empty report text provided")
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)

    chat_digest = _gather_chat_digest(chat_state)

    # Build list of paper IDs to include RAG evidence.
    paper_ids: list[str] | None

    if paper_id:
        paper_ids = [paper_id]
    else:
        paper_ids = (
            list(chat_state._histories.keys()) if chat_state._histories else None
        )

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        logger.error("LLM service unavailable – cannot generate notes")
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    provider = model_cfg["provider"]
    model_id = model_cfg["id"]

    try:
        content, media_type, filename = await NotesGenerator.generate(
            llm_service=llm_service,
            report_text=report_text,
            chat_digest=chat_digest,
            provider=provider,
            model_id=model_id,
            output_format=output_format,
            paper_ids=paper_ids,
        )
    except RuntimeError as err:
        logger.error("notes_generation_error", error=str(err))
        raise HTTPException(status_code=500, detail=str(err)) from err

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# Blog post generator
# ---------------------------------------------------------------------------

_BLOG_SYSTEM_PROMPT: str = _load_template("blog/system.md")
_NOTION_OUTLINE_PROMPT: str = _load_template("notion/outline.md")

# Default ~1 hour TTL on cached blog entries; tunable via env (config.BLOG_TTL_SECONDS).
# Cap entries to prevent unbounded growth even before TTL kicks in (config.BLOG_CACHE_MAX_ENTRIES).


@dataclass
class _BlogEntry:
    """Cached state for an in-flight or completed blog generation."""

    state: Literal["pending", "ready", "error"] = "pending"
    markdown: str = ""
    error: str = ""
    created_at: float = field(default_factory=_time.time)

    @property
    def expires_at(self) -> float:
        return self.created_at + int(config.BLOG_TTL_SECONDS)


# In-memory cache keyed by short-lived token.
_BLOG_CACHE: dict[str, _BlogEntry] = {}


def _purge_expired_blogs() -> None:
    """Drop entries past their TTL and trim oldest if cache is over capacity."""

    now = _time.time()
    for tok in list(_BLOG_CACHE.keys()):
        if _BLOG_CACHE[tok].expires_at < now:
            _BLOG_CACHE.pop(tok, None)

    cap = int(config.BLOG_CACHE_MAX_ENTRIES)
    if len(_BLOG_CACHE) > cap:
        # Drop oldest first.
        oldest = sorted(_BLOG_CACHE.items(), key=lambda kv: kv[1].created_at)
        for tok, _ in oldest[: len(_BLOG_CACHE) - cap]:
            _BLOG_CACHE.pop(tok, None)


async def _run_blog_generation(
    token: str,
    *,
    llm_service: LLMService,
    provider: str,
    model_id: str,
    report_text: str,
    chat_digest: str,
) -> None:
    """Background task that runs the LLM and stores the result in `_BLOG_CACHE`.

    Any exception is captured into the cache entry as `state="error"`; we
    intentionally swallow it here so the asyncio task does not bubble up an
    unhandled exception into the event loop.
    """

    try:
        snippet_chars = config.PAPER_MAX_CONTENT_SNIPPET_CHARS
        user_prompt = (
            "Transform the following research report into a compelling blog post.\n\n"
            "REPORT:\n" + report_text.strip()[:snippet_chars]
        )
        if chat_digest:
            user_prompt += "\n\nADDITIONAL INSIGHTS FROM CHAT:\n" + chat_digest

        max_tokens = int(config.BLOG_MAX_TOKENS)

        blog_md = await llm_service.generate_completion(
            provider_name=provider,
            model_name=model_id,
            prompt=user_prompt,
            max_tokens=max_tokens,
            system_prompt=_BLOG_SYSTEM_PROMPT,
        )

        entry = _BLOG_CACHE.get(token)
        if entry is None:
            # Entry expired or was evicted before generation finished; nothing to record.
            logger.warning("blog_generation_orphan", token=token, chars=len(blog_md))
            return
        entry.state = "ready"
        entry.markdown = blog_md
        logger.info("blog_generation_ready", token=token, chars=len(blog_md))
    except (
        Exception
    ) as err:  # noqa: BLE001 – surface any LLM/network error to the polling client
        logger.error("blog_generation_error", token=token, error=str(err))
        entry = _BLOG_CACHE.get(token)
        if entry is not None:
            entry.state = "error"
            entry.error = str(err) or "Blog generation failed"


@limiter.limit("10/5minute")
@router.post("/generate-blog")
async def generate_blog(
    request: Request,
    report_text: str = Form(...),
) -> JSONResponse:
    """Queue a blog generation and return the view URL immediately.

    The actual LLM call runs as a background task so the popup window opened by
    the click handler can be navigated within the same user gesture (avoids
    popup-blocker / auto-close issues with long-running synchronous LLM calls).
    """

    logger.info("blog_generate: request received")

    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)
    chat_digest_raw = _gather_chat_digest(chat_state, char_cap=8000)
    chat_digest = (
        deduplicate_chat(chat_digest_raw, report_text) if chat_digest_raw else ""
    )

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    _purge_expired_blogs()
    token = str(_uuid.uuid4())
    _BLOG_CACHE[token] = _BlogEntry(state="pending")

    asyncio.create_task(
        _run_blog_generation(
            token,
            llm_service=llm_service,
            provider=model_cfg["provider"],
            model_id=model_cfg["id"],
            report_text=report_text,
            chat_digest=chat_digest,
        )
    )

    logger.info("blog_queued", token=token)
    return JSONResponse({"url": f"/blog-view?token={token}", "token": token})


@router.get("/blog-status")
async def blog_status(token: str = Query(...)) -> JSONResponse:
    """Return the current state of a queued blog generation.

    Polled by `static/js/blog-view.js` until `state == "ready"` or `"error"`.
    """

    _purge_expired_blogs()
    entry = _BLOG_CACHE.get(token)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"state": "expired", "error": "Blog not found or expired."},
        )

    payload: dict[str, str] = {"state": entry.state}
    if entry.state == "ready":
        payload["markdown"] = entry.markdown
    elif entry.state == "error":
        payload["error"] = entry.error or "Blog generation failed"
    return JSONResponse(payload)


@router.get("/blog-view")
async def blog_view(
    token: str = Query(...),
) -> Response:
    """Render the blog page shell. The markdown is fetched client-side via /blog-status."""

    _purge_expired_blogs()
    entry = _BLOG_CACHE.get(token)
    if entry is None:
        raise HTTPException(
            status_code=404, detail="Blog not found or expired. Generate a new one."
        )

    safe_token = _html.escape(token, quote=True)

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blog — Lux Scholar</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/css/blog.css">
</head>
<body class="blog-is-loading">

  <!-- Reading progress bar -->
  <div class="blog-progress-bar"></div>

  <!-- Nav -->
  <nav class="blog-nav">
    <a href="/" class="blog-nav-brand">Lux Scholar</a>
    <div class="blog-nav-actions">
      <button id="blog-copy-btn" class="blog-nav-btn" title="Copy markdown">Copy MD</button>
      <button id="blog-download-btn" class="blog-nav-btn" title="Download .md">Download MD</button>
      <button id="blog-save-html-btn" class="blog-nav-btn" title="Save as HTML">Save HTML</button>
    </div>
  </nav>

  <!-- Hero -->
  <header class="blog-hero">
    <div class="blog-meta">
      <span class="blog-tag">Research</span>
      <span class="blog-meta-divider"></span>
      <span id="blog-date"></span>
      <span class="blog-meta-divider"></span>
      <span id="blog-read-time"></span>
    </div>
    <h1>Generating blog…</h1>
  </header>

  <!-- Article (rendered by JS once /blog-status returns markdown) -->
  <article class="blog-article">
    <div class="blog-loading">
      <div class="blog-loading-spinner"></div>
      <p id="blog-loading-msg">Composing your post…</p>
    </div>
  </article>

  <!-- Footer -->
  <footer class="blog-footer">
    Generated with <a href="/">Lux Scholar</a>
  </footer>

  <!-- Token consumed by blog-view.js to poll /blog-status -->
  <script>window.__BLOG_TOKEN__ = "{safe_token}";</script>
  <script src="/static/js/blog-view.js"></script>
</body>
</html>"""

    return HTMLResponse(content=page)


# ---------------------------------------------------------------------------
# Notion export
# ---------------------------------------------------------------------------


def _parse_tags(llm_output: str) -> tuple[str, list[str]]:
    """Split the TAGS: line from the outline and return (outline, tags)."""
    lines = llm_output.rstrip().split("\n")
    tags: list[str] = []
    outline = llm_output

    # Walk backwards to find the TAGS line (should be last non-empty line)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.upper().startswith("TAGS:"):
            raw = stripped[len("TAGS:") :].strip()
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            outline = "\n".join(lines[:i]).rstrip()
            break
        # Stop searching if we hit a non-TAGS content line
        break

    return outline, tags


def _notion_json_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "detail": message},
    )


@limiter.limit("10/5minute")
@router.post("/export-notion")
async def export_notion(
    request: Request,
    report_text: str = Form(...),
    title: str = Form("Lux Scholar Report"),
    source_url: str = Form(""),
    audience: str = Form(""),
    model: str = Form(""),
    report_id: str | None = Form(None),
) -> Response:
    """Generate a structured outline, then push it to Notion."""

    logger.info("notion_export: request received")

    if not report_text.strip():
        return _notion_json_error("Report text required", 400)

    from services.auth.session import get_auth_user_id
    from services.persistence.database import database_available, session_scope

    user_id = get_auth_user_id(request)
    notion_api_key = config.NOTION_API_KEY
    notion_parent_page_id = config.NOTION_PARENT_PAGE_ID
    notion_parent_database_id = config.NOTION_PARENT_DATABASE_ID
    destination = "env"

    if user_id and database_available():
        try:
            from services.persistence.repositories import (
                get_notion_integration,
                notion_access_token,
            )

            async for db_session in session_scope():
                integration = await get_notion_integration(db_session, user_id=user_id)
            if integration is not None:
                notion_api_key = notion_access_token(integration)
                notion_parent_page_id = integration.parent_page_id
                notion_parent_database_id = integration.parent_database_id
                destination = integration.workspace_name or "notion"
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "notion_user_config_load_failed", user_id=user_id, error=str(err)
            )

    if not notion_api_key:
        return _notion_json_error(
            "Notion is not connected. Connect Notion in Settings first.",
            400,
        )
    if not (notion_parent_page_id or notion_parent_database_id):
        return _notion_json_error(
            "Choose a Notion destination in Settings before exporting.",
            400,
        )

    # --- Generate structured outline via LLM ---
    llm_service: LLMService | None = get_llm_service()
    outline_md = report_text.strip()  # fallback: raw report
    tags: list[str] = []

    if llm_service:
        model_key = config.DEFAULT_MODEL_KEY
        model_cfg = config.AVAILABLE_MODELS[model_key]
        try:
            user_prompt = (
                "Distill the following research report into a structured outline.\n\n"
                "REPORT:\n"
                + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
            )
            raw_outline = await llm_service.generate_completion(
                provider_name=model_cfg["provider"],
                model_name=model_cfg["id"],
                prompt=user_prompt,
                max_tokens=1024,
                system_prompt=_NOTION_OUTLINE_PROMPT,
            )
            outline_md, tags = _parse_tags(raw_outline)
            logger.info("notion_outline_generated", chars=len(outline_md), tags=tags)
        except Exception as err:
            logger.warning("notion_outline_fallback", error=str(err))
            # Fall through — outline_md stays as raw report text
    else:
        logger.warning("notion_export: LLM unavailable, exporting raw report")

    from services.notion.notion_exporter import NotionExporter, NotionExportError

    exporter = NotionExporter(api_key=notion_api_key)
    try:
        url = await exporter.export_report(
            title=title,
            markdown=outline_md,
            parent_page_id=notion_parent_page_id,
            parent_database_id=notion_parent_database_id,
            metadata={
                "source_url": source_url or None,
                "audience": audience or None,
                "model": model or None,
                "type": "Report",
                "tags": tags,
            },
        )
    except NotionExportError as err:
        logger.error("notion_export_failed", error=str(err))
        if user_id and database_available():
            try:
                from services.persistence.repositories import record_export

                async for db_session in session_scope():
                    await record_export(
                        db_session,
                        user_id=user_id,
                        report_id=report_id,
                        export_type="notion",
                        destination=destination,
                        url=None,
                        status="failed",
                        error=str(err),
                    )
            except Exception as persist_err:  # noqa: BLE001
                logger.warning("notion_export_record_failed", error=str(persist_err))
        return _notion_json_error(str(err), 502)
    finally:
        await exporter.close()

    if user_id and database_available():
        try:
            from services.persistence.repositories import record_export

            async for db_session in session_scope():
                await record_export(
                    db_session,
                    user_id=user_id,
                    report_id=report_id,
                    export_type="notion",
                    destination=destination,
                    url=url,
                    status="success",
                )
        except Exception as persist_err:  # noqa: BLE001
            logger.warning("notion_export_record_failed", error=str(persist_err))

    logger.info("notion_export_success", url=url)
    return JSONResponse(content={"ok": True, "url": url})
