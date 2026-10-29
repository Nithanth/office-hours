"""Reproducibility report — "can this paper be reproduced?" per topic paper.

One LLM pass grounded in the paper's arXiv abstract plus a Papers With Code
repo lookup. The result caches on ``TopicPaper.metadata_json["repro_md"]`` so
re-opening the modal is instant and free.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from services.persistence.database import database_available, session_scope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reproduce"])

_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5})(?:v\d+)?")
_TIMEOUT = aiohttp.ClientTimeout(total=8)


def _esc(v: object) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _arxiv_id(url: str | None) -> str | None:
    m = _ARXIV_RE.search(url or "")
    return m.group(1) if m else None


async def _fetch_abstract(arxiv_id: str) -> str | None:
    """arXiv Atom <summary> — the same layer the text extractor uses."""
    api = f"https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
    try:
        async with (
            aiohttp.ClientSession(timeout=_TIMEOUT) as session,
            session.get(api) as resp,
        ):
            txt = await resp.text()
        m = re.search(r"<summary>(.*?)</summary>", txt, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    except Exception:
        return None


async def _fetch_code_links(arxiv_id: str) -> list[dict[str, Any]]:
    """Papers With Code repositories for the paper — [] on any failure."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                "https://paperswithcode.com/api/v1/papers/",
                params={"arxiv_id": arxiv_id},
            ) as resp:
                data = await resp.json()
            results = data.get("results") or []
            if not results:
                return []
            async with session.get(
                f"https://paperswithcode.com/api/v1/papers/{results[0]['id']}/repositories/"
            ) as resp:
                repos = await resp.json()
            if not isinstance(repos, list):
                return []
            return [
                {
                    "url": r.get("url"),
                    "official": bool(r.get("is_official")),
                    "stars": r.get("stars"),
                }
                for r in repos
                if r.get("url")
            ][:3]
    except Exception:
        return []


async def _build_report(paper: Any, request: Request) -> str | None:
    """Generate the markdown assessment; None when generation fails."""
    from api.deps import get_llm_service, get_settings
    from prompts.prompt_builder import _load_template
    from services.core import model_prefs

    llm_service = get_llm_service()
    if llm_service is None:
        return None
    settings = get_settings()

    arxiv = _arxiv_id(paper.url)
    abstract = await _fetch_abstract(arxiv) if arxiv else None
    repos = await _fetch_code_links(arxiv) if arxiv else []

    authors = paper.authors or {}
    author_names = authors.get("items") or authors.get("list") or []
    repos_text = (
        "\n".join(
            f"- {r['url']}"
            + (" (official)" if r["official"] else " (community)")
            + (f", {r['stars']} stars" if r.get("stars") else "")
            for r in repos
        )
        or "None found via Papers With Code."
    )

    prompt = _load_template("paper/reproduce.md").format(
        title=paper.title,
        authors=", ".join(str(a) for a in author_names[:8]) or "Unknown",
        url=paper.url or "",
        published=paper.published_date or "Unknown",
        abstract=abstract
        or "(abstract unavailable — assess from title and your own knowledge, and say when you're guessing)",
        code_links=repos_text,
    )

    key = settings.DEFAULT_MODEL_KEY
    result = await llm_service.generate_completion_safe(
        provider_name=settings.AVAILABLE_MODELS[key]["provider"],
        model_name=model_prefs.resolve_model_id(key),
        prompt=prompt,
        max_tokens=900,
        temperature=0.3,
    )
    if not result["ok"]:
        logger.warning("reproduce_generation_failed: %s", result["content"][:200])
        return None
    return str(result["content"])


@router.get("/repro-modal", response_class=HTMLResponse)
async def repro_modal(
    request: Request, topic_id: str = "", paper_id: str = ""
) -> HTMLResponse:
    """Modal shell — body loads the assessment on open (cached or generated)."""
    if not topic_id or not paper_id:
        raise HTTPException(status_code=400, detail="topic_id and paper_id required")
    body = f"""<div class="modal">
  <div class="modal-content">
    <div class="modal-header">
      <h2 class="chat-modal-title">Can this be reproduced?</h2>
      <button class="modal-close-btn" aria-label="Close modal"
        onclick="htmx.find('#modal-container').innerHTML = ''">&times;</button>
    </div>
    <div class="modal-body chat-log-container" id="repro-body"
         hx-get="/api/topics/{_esc(topic_id)}/reproduce?paper_id={quote(paper_id, safe='')}"
         hx-trigger="load" hx-swap="innerHTML" style="padding:1rem;">
      <div class="topic-meta">Checking the paper, its code links, and its data…</div>
    </div>
  </div>
</div>"""
    return HTMLResponse(body)


@router.get("/api/topics/{topic_id}/reproduce", response_class=HTMLResponse)
async def repro_report(
    topic_id: str, request: Request, paper_id: str = ""
) -> HTMLResponse:
    """The assessment itself — cached on the topic paper's metadata_json."""
    from services.core.model_prefs import prefs_owner_id
    from services.persistence.models import Topic, TopicPaper

    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Sign in to run this check.")
    if not paper_id:
        raise HTTPException(status_code=400, detail="paper_id required")
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    async for session in session_scope():
        row = await session.execute(
            select(TopicPaper)
            .join(Topic, TopicPaper.topic_id == Topic.id)
            .where(
                Topic.owner_id == owner,
                TopicPaper.topic_id == topic_id,
                TopicPaper.paper_id == paper_id,
            )
            .limit(1)
        )
        paper = row.scalars().first()
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        md = (paper.metadata_json or {}).get("repro_md")
        if not md:
            md = await _build_report(paper, request)
            if md is None:
                return HTMLResponse(
                    '<div class="topic-meta">Couldn\'t assess this paper right now — '
                    "the model call failed. Try again in a moment.</div>"
                )
            paper.metadata_json = {**(paper.metadata_json or {}), "repro_md": md}
            await session.commit()

    from utils.md_render import render_markdown_safe

    return HTMLResponse(
        f'<div class="blog-scope"><article class="blog-article">'
        f"{render_markdown_safe(md)}</article></div>"
    )
