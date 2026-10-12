"""Author search routes — search by researcher name via Semantic Scholar.

Provides two endpoints:
- POST /author/search  — find authors by name, render result cards
- POST /author/profile — get full profile + papers for a selected author
- POST /author/research-profile — generate LLM research profile summary
"""

from __future__ import annotations

import asyncio
import html

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_llm_service, get_session_id, get_settings
from config import Config as AppConfig
from models.paper import Paper
from pages.main_page import render_search_results
from services.paper.metadata.author_search import (
    AuthorResult,
    get_author_profile,
    get_paper_url,
    search_authors,
)
from utils.rate_limit import limiter

router = APIRouter(prefix="/author", tags=["author"])


def _render_author_card(author: AuthorResult) -> str:
    """Render a single author result card as an HTML fragment."""
    name_esc = html.escape(author.name)
    aliases_str = ""
    if author.aliases:
        visible = author.aliases[:3]
        aliases_str = f"<div style='font-size:0.8rem;opacity:0.7;margin-top:2px;'>Also: {html.escape(', '.join(visible))}</div>"

    # Metrics row
    metrics = (
        f"<span title='Papers'>{author.paper_count:,} papers</span>"
        f" &middot; <span title='Citations'>{author.citation_count:,} citations</span>"
        f" &middot; <span title='h-index'>h-index {author.h_index}</span>"
    )

    return (
        f"<div class='author-card' style='padding:12px;border:1px solid var(--lux-border,#444);"
        f"border-radius:8px;margin-bottom:8px;cursor:pointer;transition:border-color 0.15s;'"
        f" hx-post='/author/profile' hx-target='#search-results-container' hx-swap='innerHTML'"
        f' hx-vals=\'{{"author_id": "{html.escape(author.author_id)}", "author_name": "{name_esc}"}}\''
        f" hx-indicator='#author-loading'>"
        f"<div style='font-size:1.1rem;font-weight:600;'>{name_esc}</div>"
        f"{aliases_str}"
        f"<div style='font-size:0.85rem;margin-top:4px;opacity:0.85;'>{metrics}</div>"
        f"</div>"
    )


def _render_author_list(authors: list[AuthorResult], query: str) -> str:
    """Render the full author search results panel."""
    if not authors:
        return (
            "<div style='padding:24px;text-align:center;border:1px dashed var(--lux-border,#444);"
            "border-radius:8px;margin-top:12px;'>"
            f"<p style='opacity:0.7;'>No authors found for \"{html.escape(query)}\". "
            "Try a different name or spelling.</p></div>"
        )

    cards = "\n".join(_render_author_card(a) for a in authors)
    return (
        f"<div id='author-results'>"
        f"<p style='margin-bottom:8px;font-size:0.9rem;opacity:0.7;'>"
        f'Found {len(authors)} author(s) for "{html.escape(query)}" — click to explore</p>'
        f"<div id='author-loading' class='htmx-indicator' style='text-align:center;padding:12px;'>"
        f"<span class='spinner'></span> Loading author profile…</div>"
        f"{cards}"
        f"</div>"
    )


@limiter.limit("30/5minute")
@router.post("/search", response_class=HTMLResponse)
async def author_search(
    request: Request,
    search_query: str = Form(""),
    settings: AppConfig = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Search for authors by name using Semantic Scholar."""
    session_id = await get_session_id(request)
    query = search_query.strip()

    if not query:
        return HTMLResponse(
            "<div class='error-banner'>Please enter an author name.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    if len(query) > 200:
        query = query[:200]

    logger.info("author_search_request", query=query, session_id=session_id)

    try:
        authors = await asyncio.to_thread(search_authors, query, limit=10, timeout=10)
    except Exception as exc:
        logger.exception("author_search_failed", error=str(exc))
        return HTMLResponse(
            "<div class='error-banner'>Author search failed. Please try again.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    results_html = _render_author_list(authors, query)
    return HTMLResponse(
        content=results_html,
        headers={
            "HX-Retarget": "#search-results-container",
            "HX-Reswap": "innerHTML",
            "HX-Push-Url": "/",
        },
    )


@limiter.limit("20/5minute")
@router.post("/profile", response_class=HTMLResponse)
async def author_profile_view(
    request: Request,
    author_id: str = Form(...),
    author_name: str = Form(""),
) -> HTMLResponse:
    """Fetch and render full author profile with papers.

    Papers are converted to the standard Paper model and rendered using
    the existing search results UI, so they integrate seamlessly with
    the report generation and chat flows.
    """
    logger.info("author_profile_request", author_id=author_id, name=author_name)

    try:
        profile = await asyncio.to_thread(
            get_author_profile, author_id, paper_limit=30, timeout=15
        )
    except Exception as exc:
        logger.exception("author_profile_failed", error=str(exc))
        return HTMLResponse(
            "<div class='error-banner'>Could not load author profile.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    if profile is None:
        return HTMLResponse(
            f"<div class='error-banner'>Author \"{html.escape(author_name)}\" not found.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    # Build header with author stats + generate profile button
    a = profile.author
    header_html = (
        f"<div style='margin-bottom:16px;padding:16px;border:1px solid var(--lux-border,#444);"
        f"border-radius:10px;background:var(--lux-card-bg,rgba(255,255,255,0.03));'>"
        f"<div style='display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;'>"
        f"<div>"
        f"<h2 style='margin:0;font-size:1.3rem;'>{html.escape(a.name)}</h2>"
        f"<div style='font-size:0.85rem;opacity:0.7;margin-top:4px;'>"
        f"{a.paper_count:,} papers &middot; {a.citation_count:,} citations &middot; h-index {a.h_index}"
        f"</div>"
        f"</div>"
        f"<button class='btn-primary' style='font-size:0.85rem;padding:6px 14px;'"
        f" hx-post='/author/research-profile'"
        f" hx-target='#author-profile-container'"
        f" hx-swap='innerHTML'"
        f' hx-vals=\'{{"author_id": "{html.escape(a.author_id)}", "author_name": "{html.escape(a.name)}"}}\''
        f" hx-indicator='#profile-loading'>"
        f"Generate Research Profile</button>"
        f"</div>"
        f"<div id='profile-loading' class='htmx-indicator' style='text-align:center;padding:8px;'>"
        f"<span class='spinner'></span> Generating research profile…</div>"
        f"<div id='author-profile-container' style='margin-top:12px;'></div>"
        f"</div>"
    )

    # Convert S2 papers to our Paper model for reuse with existing UI
    papers: list[Paper] = []
    for sp in profile.papers:
        url = get_paper_url(sp)
        if not url:
            continue
        papers.append(
            Paper(
                id=sp.paper_id or url,
                title=sp.title,
                url=url,
                authors=[a.name],
                published_date=f"{sp.year}-01-01" if sp.year else None,
                citation_count=sp.citation_count,
                source="Semantic Scholar",
                text_content=sp.abstract,
            )
        )

    if not papers:
        return HTMLResponse(
            header_html + "<div style='padding:16px;text-align:center;opacity:0.7;'>"
            "No papers with accessible URLs found for this author.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    # Render using standard search results component
    results_component = render_search_results(
        papers_list=papers,
        is_url_search=False,
        original_query=f"Author: {a.name}",
    )

    return HTMLResponse(
        content=header_html + str(results_component),
        headers={
            "HX-Retarget": "#search-results-container",
            "HX-Reswap": "innerHTML",
            "HX-Push-Url": "/",
        },
    )


@limiter.limit("10/5minute")
@router.post("/research-profile", response_class=HTMLResponse)
async def generate_research_profile(
    request: Request,
    author_id: str = Form(...),
    author_name: str = Form(""),
) -> HTMLResponse:
    """Generate an LLM-powered research profile summary for an author."""
    from prompts.prompt_builder import _load_template

    llm_service = get_llm_service()
    settings = get_settings()

    if llm_service is None or not llm_service._initialized:
        return HTMLResponse(
            "<div class='error-banner'>LLM service not available.</div>",
            status_code=503,
        )

    logger.info("research_profile_request", author_id=author_id, name=author_name)

    # Fetch profile data
    try:
        profile = await asyncio.to_thread(
            get_author_profile, author_id, paper_limit=20, timeout=15
        )
    except Exception as exc:
        logger.exception("research_profile_fetch_failed", error=str(exc))
        return HTMLResponse(
            "<div class='error-banner'>Could not fetch author data.</div>"
        )

    if profile is None:
        return HTMLResponse("<div class='error-banner'>Author not found.</div>")

    # Build papers summary for the prompt
    papers_lines: list[str] = []
    for i, p in enumerate(profile.papers[:15], 1):
        venue_str = f" ({p.venue})" if p.venue else ""
        year_str = f" [{p.year}]" if p.year else ""
        papers_lines.append(
            f'{i}. "{p.title}"{year_str}{venue_str} — {p.citation_count:,} citations'
        )
    papers_summary = "\n".join(papers_lines) if papers_lines else "No papers available."

    # Load and fill prompt template
    template = _load_template("author/profile.md")
    prompt = template.format(
        author_name=profile.author.name,
        paper_count=profile.author.paper_count,
        citation_count=profile.author.citation_count,
        h_index=profile.author.h_index,
        papers_summary=papers_summary,
    )

    # Generate via default model
    mi = settings.AVAILABLE_MODELS[settings.DEFAULT_MODEL_KEY]
    try:
        result = await llm_service.generate_completion(
            provider_name=mi["provider"],
            model_name=mi["id"],
            prompt=prompt,
            max_tokens=1024,
            temperature=0.3,
        )
    except Exception as exc:
        logger.exception("research_profile_generation_failed", error=str(exc))
        return HTMLResponse(
            "<div class='error-banner'>Could not generate research profile. Try again.</div>"
        )

    # Render markdown to HTML
    try:
        import markdown

        profile_html = markdown.markdown(result, extensions=["extra", "sane_lists"])
    except ImportError:
        profile_html = f"<pre style='white-space:pre-wrap;'>{html.escape(result)}</pre>"

    return HTMLResponse(
        f"<div class='research-profile' style='padding:16px;border:1px solid var(--lux-border,#444);"
        f"border-radius:8px;line-height:1.6;'>"
        f"<h3 style='margin-top:0;'>Research Profile: {html.escape(profile.author.name)}</h3>"
        f"{profile_html}"
        f"</div>"
    )
