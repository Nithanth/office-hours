"""'More like this' endpoint — find papers similar to a given one.

Returns an HTML fragment suitable for inline HTMX expansion below
the anchor paper card.
"""

from __future__ import annotations

import asyncio
import html
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_exa_client
from models.paper import Paper
from services.core.exa_client import ExaClient
from utils.rate_limit import limiter

router = APIRouter(prefix="/api", tags=["similar"])

# Maximum recommendations per request
_MAX_SIMILAR = 5


@limiter.limit("30/5minute")
@router.post("/similar", response_class=HTMLResponse)
async def find_similar(
    request: Request,
    paper_url: str = Form(...),
    paper_title: str = Form(""),
) -> HTMLResponse:
    """Find papers similar to the given URL.

    Returns an HTML fragment with recommendation cards that can be
    inserted inline below the anchor paper.
    """
    exa_client = get_exa_client()

    if exa_client is None:
        return HTMLResponse(
            _error_fragment("Search service not available."),
            status_code=503,
        )

    client = cast(ExaClient, exa_client)

    try:
        similar_papers = await asyncio.to_thread(
            client.find_similar_papers,
            paper_url=paper_url,
            num_results=_MAX_SIMILAR,
        )
    except Exception as exc:
        logger.warning("find_similar_failed", url=paper_url, err=str(exc))
        return HTMLResponse(_error_fragment(f"Could not find similar papers: {exc}"))

    if not similar_papers:
        return HTMLResponse(_error_fragment("No similar papers found."))

    # Best-effort S2 citation enrichment
    try:
        from services.paper.metadata.semantic_scholar import (
            enrich_papers_with_citations,
        )

        await asyncio.to_thread(enrich_papers_with_citations, similar_papers, timeout=6)
    except Exception as exc:
        logger.debug("similar_citation_enrichment_skipped", err=str(exc))

    # Build the inline recommendation fragment
    title_display = html.escape(paper_title[:80]) if paper_title else "this paper"
    fragment = _render_similar_fragment(similar_papers, anchor_title=title_display)
    return HTMLResponse(fragment)


def _error_fragment(msg: str) -> str:
    """Minimal error HTML for the recommendation panel."""
    return (
        f"<div class='similar-panel' style='padding:8px 12px;margin:6px 0 6px 24px;"
        f"font-size:0.85rem;color:var(--fg-muted,#888);border-left:2px solid var(--container-border,#ddd);'>"
        f"{html.escape(msg)}</div>"
    )


def _render_similar_fragment(papers: list[Paper], anchor_title: str) -> str:
    """Render the recommendation panel HTML."""
    items: list[str] = []

    for pap in papers:
        url_str = str(pap.url) if pap.url else "#"
        title_str = pap.title or "Untitled"
        paper_json = html.escape(pap.model_dump_json())

        # Authors (compact)
        authors = ""
        if pap.authors:
            names = pap.authors[:3]
            suffix = " et al." if len(pap.authors) > 3 else ""
            authors = html.escape(", ".join(names) + suffix)

        # Year
        year = ""
        if pap.published_date:
            try:
                if isinstance(pap.published_date, datetime):
                    year = str(pap.published_date.year)
                else:
                    year = str(pap.published_date)[:4]
            except Exception:
                pass

        # Citation count
        cite_str = ""
        if pap.citation_count and pap.citation_count > 0:
            cite_str = f" &middot; {pap.citation_count:,} cites"

        # Meta line: authors · year · citations
        meta_parts = [x for x in [authors, year] if x]
        meta_line = " &middot; ".join(meta_parts) + cite_str

        items.append(
            f"<li style='margin-bottom:8px;padding:4px 0;list-style:none;'>"
            f"<label style='display:flex;align-items:flex-start;gap:6px;cursor:pointer;'>"
            f"<input type='checkbox' name='selected_papers_json' value='{paper_json}' "
            f"class='paper-select-checkbox similar-paper-checkbox' style='margin-top:3px;'>"
            f"<span>"
            f"<a href='{html.escape(url_str)}' target='_blank' rel='noopener noreferrer' "
            f"style='font-size:0.9rem;'>{html.escape(title_str)}</a><br>"
            f"<span style='font-size:0.78rem;color:var(--fg-muted,#888);'>{meta_line}</span>"
            f"</span>"
            f"</label>"
            f"</li>"
        )

    items_html = "\n".join(items)

    return (
        f"<div class='similar-panel' style='"
        f"margin:8px 0 8px 24px;padding:10px 14px;"
        f"border-left:3px solid var(--accent,#4e79a7);"
        f"background:var(--bg-subtle,rgba(78,121,167,0.04));"
        f"border-radius:0 6px 6px 0;"
        f"'>"
        f"<div style='font-size:0.8rem;font-weight:600;color:var(--fg-muted,#888);"
        f"margin-bottom:6px;'>"
        f"<i class='fa-solid fa-wand-magic-sparkles' style='margin-right:4px;'></i>"
        f"Similar to {anchor_title}"
        f"</div>"
        f"<ul style='margin:0;padding:0;'>{items_html}</ul>"
        f"</div>"
    )
