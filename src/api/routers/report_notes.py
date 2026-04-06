"""Study-notes and blog-post generators.

POST `/generate-report-notes` – downloadable notes (`.txt`, `.md`, or `.docx`).
POST `/generate-blog` – downloadable Markdown blog post synthesised from the report.
"""

from typing import Literal

from fastapi import APIRouter, Form, HTTPException, Request, Response
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


@limiter.limit("10/5minute")
@router.post("/generate-blog")
async def generate_blog(
    request: Request,
    report_text: str = Form(...),
) -> Response:
    """Generate a polished Markdown blog post from the report text + chat history."""

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

    headers = {"Content-Disposition": 'attachment; filename="research_blog.md"'}
    return Response(blog_md, media_type="text/markdown; charset=utf-8", headers=headers)
