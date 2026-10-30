"""Author search routes — search by researcher name via Semantic Scholar.

Provides two endpoints:
- POST /author/search  — find authors by name, render result cards
- POST /author/profile — get full profile + papers for a selected author
- POST /author/research-profile — generate LLM research profile summary
"""

from __future__ import annotations

import asyncio
import html
import json as _json
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from api.deps import get_llm_service, get_session_id, get_settings
from config import Config as AppConfig
from models.paper import Paper
from pages.main_page import render_search_results
from services.core import model_prefs
from services.paper.metadata.author_search import (
    AuthorPaper,
    AuthorProfile,
    AuthorResult,
    S2UnavailableError,
    get_author_profile,
    get_paper_url,
    search_authors,
)
from utils.rate_limit import limiter

router = APIRouter(prefix="/author", tags=["author"])


def _render_author_card(author: AuthorResult, keyword: str | None = None) -> str:
    """Render a single author result card as an HTML fragment."""
    name_esc = html.escape(author.name)
    aliases_str = ""
    if author.aliases:
        visible = author.aliases[:3]
        # unescape first — S2 names arrive entity-encoded and escape() would
        # double-encode the '&' ("Bottou L&eacute;on" renders literally).
        aliases_str = f"<div style='font-size:0.8rem;opacity:0.7;margin-top:2px;'>Also: {html.escape(html.unescape(', '.join(visible)))}</div>"

    # Metrics row
    metrics = (
        f"<span title='Papers'>{author.paper_count:,} papers</span>"
        f" &middot; <span title='Citations'>{author.citation_count:,} citations</span>"
        f" &middot; <span title='h-index'>h-index {author.h_index}</span>"
    )

    # hx-vals is JSON inside a single-quoted attr — dump it then escape so
    # quotes in names/keywords can't break either layer.
    vals: dict[str, str] = {
        "author_id": author.author_id,
        "author_name": author.name,
    }
    if keyword:
        vals["keyword"] = keyword
    esc_vals = html.escape(_json.dumps(vals), quote=True)
    return (
        f"<div class='author-card' style='padding:12px;border:1px solid var(--oh-border,#444);"
        f"border-radius:8px;margin-bottom:8px;cursor:pointer;transition:border-color 0.15s;'"
        f" hx-post='/author/profile' hx-target='#search-results-container' hx-swap='innerHTML'"
        f" hx-vals='{esc_vals}'"
        f" hx-indicator='#author-loading'>"
        f"<div style='font-size:1.1rem;font-weight:600;'>{name_esc}</div>"
        f"{aliases_str}"
        f"<div style='font-size:0.85rem;margin-top:4px;opacity:0.85;'>{metrics}</div>"
        f"</div>"
    )


def _render_author_list(
    authors: list[AuthorResult],
    query: str,
    unavailable: bool = False,
    keyword: str | None = None,
) -> str:
    """Render the full author search results panel."""
    if unavailable:
        return (
            "<div style='padding:24px;text-align:center;border:1px dashed var(--oh-border,#444);"
            "border-radius:8px;margin-top:12px;'>"
            "<p style='opacity:0.7;'>Author search is temporarily unavailable. "
            "Semantic Scholar is rate-limiting requests. "
            f'Try "{html.escape(query)}" again in a minute.</p></div>'
        )
    if not authors:
        return (
            "<div style='padding:24px;text-align:center;border:1px dashed var(--oh-border,#444);"
            "border-radius:8px;margin-top:12px;'>"
            f"<p style='opacity:0.7;'>No authors found for \"{html.escape(query)}\". "
            "Try a different name or spelling.</p></div>"
        )

    cards = "\n".join(_render_author_card(a, keyword) for a in authors)
    return (
        f"<div id='author-results'>"
        f"<p style='margin-bottom:8px;font-size:0.9rem;opacity:0.7;'>"
        f'Found {len(authors)} authors for "{html.escape(query)}". Click a card to see their work.</p>'
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
            "<div class='oh-toast'>Please enter an author name.</div>",
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
    except S2UnavailableError as exc:
        logger.warning("author_search_unavailable", error=str(exc))
        return HTMLResponse(
            _render_author_list([], query, unavailable=True),
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )
    except Exception as exc:
        logger.exception("author_search_failed", error=str(exc))
        return HTMLResponse(
            "<div class='oh-toast'>Author search failed. Please try again.</div>",
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
    keyword: str = Form(""),
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
            "<div class='oh-toast'>Could not load author profile.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    if profile is None:
        return HTMLResponse(
            f"<div class='oh-toast'>Author \"{html.escape(author_name)}\" not found.</div>",
            headers={
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    return HTMLResponse(
        content=_author_profile_html(profile, keyword=keyword.strip() or None),
        headers={
            "HX-Retarget": "#search-results-container",
            "HX-Reswap": "innerHTML",
            "HX-Push-Url": "/",
        },
    )


def _paper_matches_keyword(sp: AuthorPaper, keyword: str) -> bool:
    """All keyword tokens must appear in the paper's title/abstract/venue —
    triangulation should only surface works that name every term. Lookarounds
    (not \\b) so "ai" can't hit "training" but "C++" and "C#" still match:
    a guard is only placed where the token's edge is a word char, so
    versioned forms like "c++17" keep matching while inside-word hits fail."""
    hay = " ".join([sp.title or "", sp.abstract or "", sp.venue or ""]).lower()

    def _pattern(tok: str) -> str:
        pat = re.escape(tok)
        if re.match(r"\w", tok[0]):
            pat = r"(?<!\w)" + pat
        if re.match(r"\w", tok[-1]):
            pat += r"(?!\w)"
        return pat

    return all(
        re.search(_pattern(tok), hay) is not None for tok in keyword.lower().split()
    )


def _author_profile_html(profile: AuthorProfile, *, keyword: str | None = None) -> str:
    """Header + publication list for an author profile.

    *keyword* pre-filters the list server-side ("papers by X about Y"); the
    filter input then narrows it further client-side — the loaded page is a
    small set, so live filtering needs no round trip.
    """
    a = profile.author
    filter_chip = (
        f"<div style='font-size:0.8rem;opacity:0.75;margin-top:6px;'>"
        f"filtered by &ldquo;{html.escape(keyword)}&rdquo;</div>"
        if keyword
        else ""
    )
    header_html = (
        f"<div style='margin-bottom:16px;padding:16px;border:1px solid var(--oh-border,#444);"
        f"border-radius:10px;background:var(--oh-card-bg,rgba(255,255,255,0.03));'>"
        f"<div style='display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;'>"
        f"<div>"
        f"<h2 style='margin:0;font-size:1.3rem;'>{html.escape(html.unescape(a.name))}</h2>"
        f"<div style='font-size:0.85rem;opacity:0.7;margin-top:4px;'>"
        f"{a.paper_count:,} papers &middot; {a.citation_count:,} citations &middot; h-index {a.h_index}"
        f"</div>"
        f"{filter_chip}"
        f"</div>"
        f"<button class='btn-secondary'"
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
    source_papers = profile.papers
    if keyword:
        source_papers = [p for p in source_papers if _paper_matches_keyword(p, keyword)]
    papers: list[Paper] = []
    for sp in source_papers:
        url = get_paper_url(sp)
        if not url:
            continue
        papers.append(
            Paper(
                id=sp.paper_id or url,
                title=sp.title,
                url=url,
                authors=sp.authors or [a.name],
                published_date=f"{sp.year}-01-01" if sp.year else None,
                citation_count=sp.citation_count,
                source="Semantic Scholar",
                text_content=sp.abstract,
            )
        )

    if not papers:
        empty_msg = (
            f'No papers by {html.escape(a.name)} match "{html.escape(keyword)}".'
            if keyword
            else "No papers with accessible URLs found for this author."
        )
        return (
            header_html + "<div style='padding:16px;text-align:center;opacity:0.7;'>"
            f"{empty_msg}</div>"
        )

    # No prefill on the results filter: it narrows by title/authors only,
    # while the server match also covers abstract/venue — prefilling would
    # hide papers the server deliberately kept. The chip carries the keyword.
    results_component = render_search_results(
        papers_list=papers,
        is_url_search=False,
        original_query=f"Author: {a.name}",
    )
    return header_html + str(results_component)


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
            "<div class='oh-toast'>LLM service not available.</div>",
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
        return HTMLResponse("<div class='oh-toast'>Could not fetch author data.</div>")

    if profile is None:
        return HTMLResponse("<div class='oh-toast'>Author not found.</div>")

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
            model_name=model_prefs.resolve_model_id(settings.DEFAULT_MODEL_KEY),
            prompt=prompt,
            max_tokens=1024,
            temperature=0.3,
        )
    except Exception as exc:
        logger.exception("research_profile_generation_failed", error=str(exc))
        return HTMLResponse(
            "<div class='oh-toast'>Could not generate research profile. Try again.</div>"
        )

    # Render markdown to HTML
    try:
        import markdown

        profile_html = markdown.markdown(result, extensions=["extra", "sane_lists"])
    except ImportError:
        profile_html = f"<pre style='white-space:pre-wrap;'>{html.escape(result)}</pre>"

    return HTMLResponse(
        f"<div class='research-profile' style='padding:16px;border:1px solid var(--oh-border,#444);"
        f"border-radius:8px;line-height:1.6;'>"
        f"<h3 style='margin-top:0;'>Research Profile: {html.escape(profile.author.name)}</h3>"
        f"{profile_html}"
        f"</div>"
    )
