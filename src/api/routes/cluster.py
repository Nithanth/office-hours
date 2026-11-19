"""Paper clustering API endpoint.

Computes 2D UMAP coordinates and HDBSCAN cluster assignments for the
current session's search results, suitable for rendering as an
interactive scatter plot.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from api.deps import get_exa_client, get_session_id
from services.core.exa_client import ExaClient
from utils.rate_limit import limiter

router = APIRouter(prefix="/api", tags=["cluster"])


@limiter.limit("10/5minute")
@router.post("/cluster")
async def cluster_papers_endpoint(
    request: Request,
    scope: str = "search",
    topic_id: str | None = None,
) -> JSONResponse:
    """Compute paper clusters for the current session's search results.

    Returns 2D coordinates (UMAP) and cluster assignments for each paper,
    plus auto-generated cluster labels.

    ``scope=library`` clusters the caller's saved papers instead of the
    search pool — a single query's results rarely have meaningful
    sub-structure, the library does once it has some mass.
    """
    session_id = await get_session_id(request)
    exa_client = get_exa_client()

    if exa_client is None:
        return JSONResponse(
            {"ok": False, "error": "Search service not available"},
            status_code=503,
        )

    client = cast(ExaClient, exa_client)

    if scope == "topic":
        if not topic_id:
            return JSONResponse(
                {"ok": False, "error": "Choose a topic first."}, status_code=400
            )
        paper_dicts = await _topic_paper_dicts(request, topic_id)
        if paper_dicts is None:
            return JSONResponse(
                {"ok": False, "error": "Topic not found."}, status_code=404
            )
        if len(paper_dicts) < 2:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Add at least 2 papers to explore this topic's map.",
                },
                status_code=400,
            )
    elif scope == "library":
        paper_dicts = await _library_paper_dicts(request, client)
        if paper_dicts is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "Save at least 4 papers to visualize your library.",
                },
                status_code=400,
            )
    else:
        cached_search = client.get_cached_search(session_id)

        if cached_search is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "No search results to cluster. Run a search first.",
                },
                status_code=404,
            )

        # Get all papers currently in the cache pool
        display_papers = cached_search.get_display_papers()
        if not display_papers or len(display_papers) < 2:
            return JSONResponse(
                {"ok": False, "error": "Need at least 2 papers to cluster."},
                status_code=400,
            )

        # Batch-fetch highlights for richer embeddings (best-effort)
        paper_dicts = await _prepare_paper_dicts(client, display_papers)

    # Run clustering in thread pool (CPU-bound: embedding + UMAP + HDBSCAN)
    try:
        from services.paper.clustering import cluster_papers

        result = await asyncio.to_thread(cluster_papers, paper_dicts)
    except Exception as exc:  # noqa: BLE001 - surface honestly, not a bare 500
        logger.warning("cluster_failed", err=str(exc))
        return JSONResponse(
            {"ok": False, "error": "Clustering is temporarily unavailable."},
            status_code=503,
        )

    metadata = {paper["id"]: paper for paper in paper_dicts}
    return JSONResponse(
        {
            "ok": True,
            "papers": [
                {
                    **paper,
                    "status": metadata.get(paper["id"], {}).get("status", "to_read"),
                    "url": metadata.get(paper["id"], {}).get("url", ""),
                }
                for paper in result.papers
            ],
            "clusters": result.clusters,
        }
    )


async def _topic_paper_dicts(
    request: Request, topic_id: str
) -> list[dict[str, Any]] | None:
    """Only cluster papers belonging to a topic the current owner can read."""
    from services.core.model_prefs import prefs_owner_id
    from services.persistence.database import database_available, session_scope
    from services.persistence.repositories import (
        get_topic,
        list_saved_statuses,
        list_topic_papers,
    )

    owner = prefs_owner_id(request)
    if not owner or not database_available():
        return None
    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            return None
        papers = await list_topic_papers(session, topic_id=topic.id)
        statuses = await list_saved_statuses(
            session, owner_id=owner, paper_ids=[p.paper_id for p in papers]
        )
        return [
            {
                "id": p.paper_id,
                "title": p.title or "",
                "url": p.url or "",
                "authors": (
                    (p.authors.get("items") or p.authors.get("list") or [])
                    if isinstance(p.authors, dict)
                    else (p.authors or [])
                ),
                # TopicPaper stores ISO publication dates as strings, not dates.
                "year": str(p.published_date or "")[:4],
                "highlights": "",
                "citation_count": None,
                "status": statuses.get(p.paper_id, "to_read"),
            }
            for p in papers
        ]
    return None


async def _library_paper_dicts(
    request: Request,
    client: ExaClient,
) -> list[dict[str, Any]] | None:
    """Build cluster input dicts from the caller's saved papers.

    Returns None when there are fewer than 4 saved papers — below that the
    scatter is degenerate anyway.
    """
    from services.core.model_prefs import prefs_owner_id
    from services.persistence.database import session_scope
    from services.persistence.repositories import list_saved_papers

    owner_id = prefs_owner_id(request)
    if not owner_id:
        return None

    saved: list[Any] = []
    async for session in session_scope():
        saved = await list_saved_papers(session, owner_id=owner_id)

    if len(saved) < 4:
        return None

    urls = [p.url for p in saved if p.url]
    highlights_map: dict[str, str] = {}
    if urls:
        try:
            highlights_map = await asyncio.to_thread(
                _batch_fetch_highlights, client, urls
            )
        except Exception as exc:
            logger.warning("cluster_highlights_fetch_failed", err=str(exc))

    dicts: list[dict[str, Any]] = []
    for p in saved:
        authors = p.authors.get("list") if isinstance(p.authors, dict) else None
        dicts.append(
            {
                "id": p.paper_id or "",
                "title": p.title or "",
                "authors": authors or [],
                "year": "",
                "highlights": highlights_map.get(p.url or "", ""),
                "citation_count": None,
            }
        )
    return dicts


async def _prepare_paper_dicts(
    client: ExaClient, papers: list[Any]
) -> list[dict[str, Any]]:
    """Build paper dicts for clustering, enriching with highlights where possible."""
    paper_dicts: list[dict[str, Any]] = []

    # Try to batch-fetch highlights for all papers (best-effort, non-blocking)
    urls = [str(p.url) for p in papers if p.url]
    highlights_map: dict[str, str] = {}

    if urls:
        try:
            highlights_map = await asyncio.to_thread(
                _batch_fetch_highlights, client, urls
            )
        except Exception as exc:
            logger.warning("cluster_highlights_fetch_failed", err=str(exc))

    for p in papers:
        url_str = str(p.url) if p.url else ""
        year = ""
        if p.published_date:
            with suppress(TypeError, AttributeError):
                year = str(p.published_date)[:4]

        paper_dicts.append(
            {
                "id": p.id or "",
                "title": p.title or "",
                "authors": p.authors or [],
                "year": year,
                "highlights": highlights_map.get(url_str, ""),
                "citation_count": p.citation_count,
            }
        )

    return paper_dicts


def _batch_fetch_highlights(client: ExaClient, urls: list[str]) -> dict[str, str]:
    """Fetch highlights for a batch of URLs. Returns url→highlights_text map."""
    highlights: dict[str, str] = {}

    try:
        res = client.client.get_contents(urls, highlights=True)
        for result in getattr(res, "results", []) or []:
            url = getattr(result, "url", "")
            hl = getattr(result, "highlights", None)
            if url and hl:
                if isinstance(hl, list):
                    highlights[url] = " ".join(str(h) for h in hl)
                else:
                    highlights[url] = str(hl)
    except Exception as exc:
        logger.warning("batch_highlights_failed", err=str(exc), n_urls=len(urls))

    logger.debug("highlights_fetched", total=len(urls), found=len(highlights))
    return highlights
