"""Study-notes and blog-post generators.

POST `/generate-report-notes` – downloadable notes (`.txt`, `.md`, or `.docx`).
POST `/generate-blog` – generates a Markdown blog and redirects to a rendered view.
GET  `/blog-view` – standalone rendered blog page.
"""

import html as _html
import uuid as _uuid
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_llm_service, get_session_id
from config import config
from prompts.prompt_builder import _load_template
from services.chat.chat_state import ChatState, get_chat_state
from services.core.llm_service import LLMService
from services.notes.notes_generator import NotesGenerator
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
        paper_ids = list(chat_state._histories.keys()) if chat_state._histories else None

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

# In-memory store for generated blog markdown (token → markdown string).
# Entries are short-lived — consumed once when /blog-view loads.
_BLOG_CACHE: dict[str, str] = {}


@limiter.limit("10/5minute")
@router.post("/generate-blog")
async def generate_blog(
    request: Request,
    report_text: str = Form(...),
) -> Response:
    """Generate a Markdown blog post and return a token for the rendered view."""

    logger.info("blog_generate: request received")

    if not report_text.strip():
        raise HTTPException(status_code=400, detail="Report text required")

    session_id = await get_session_id(request)
    chat_state = get_chat_state(session_id)
    chat_digest = _gather_chat_digest(chat_state, char_cap=8000)

    llm_service: LLMService | None = get_llm_service()
    if llm_service is None:
        raise HTTPException(status_code=503, detail="LLM service unavailable")

    model_key = config.DEFAULT_MODEL_KEY
    model_cfg = config.AVAILABLE_MODELS[model_key]

    user_prompt = (
        "Transform the following research report into a compelling blog post.\n\n"
        "REPORT:\n"
        + report_text.strip()[: config.PAPER_MAX_CONTENT_SNIPPET_CHARS]
    )
    if chat_digest:
        user_prompt += "\n\nADDITIONAL INSIGHTS FROM CHAT:\n" + chat_digest

    try:
        blog_md = await llm_service.generate_completion(
            provider_name=model_cfg["provider"],
            model_name=model_cfg["id"],
            prompt=user_prompt,
            max_tokens=4096,
            system_prompt=_BLOG_SYSTEM_PROMPT,
        )
    except Exception as err:
        logger.error("blog_generation_error", error=str(err))
        raise HTTPException(status_code=500, detail="Blog generation failed") from err

    # Store markdown under a one-time token and return the view URL.
    token = str(_uuid.uuid4())
    _BLOG_CACHE[token] = blog_md
    logger.info("blog_cached", token=token, chars=len(blog_md))

    # Return JSON so the frontend JS can open the blog view in a new tab.
    return Response(
        content=f'{{"url": "/blog-view?token={token}"}}',
        media_type="application/json",
    )


@router.get("/blog-view")
async def blog_view(
    token: str = Query(...),
) -> Response:
    """Render the blog post as a standalone modern page."""

    blog_md = _BLOG_CACHE.pop(token, None)
    if blog_md is None:
        raise HTTPException(status_code=404, detail="Blog not found or already viewed. Generate a new one.")

    # Escape markdown for safe embedding in HTML
    safe_md = _html.escape(blog_md)

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
      <button id="blog-copy-btn" class="blog-nav-btn" title="Copy markdown">Copy</button>
      <button id="blog-download-btn" class="blog-nav-btn" title="Download .md">Download</button>
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
    <h1>Loading...</h1>
  </header>

  <!-- Article (rendered by JS) -->
  <article class="blog-article">
    <div class="blog-loading">
      <div class="blog-loading-spinner"></div>
      <p>Rendering blog...</p>
    </div>
  </article>

  <!-- Footer -->
  <footer class="blog-footer">
    Generated with <a href="/">Lux Scholar</a>
  </footer>

  <!-- Raw markdown (hidden, consumed by JS) -->
  <script id="blog-raw" type="text/plain">{safe_md}</script>

  <script src="/static/js/blog-view.js"></script>
</body>
</html>"""

    return HTMLResponse(content=page)
