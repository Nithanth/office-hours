"""Paper clustering API endpoint.

Computes 2D t-SNE coordinates and cluster assignments for the current
session's search results, suitable for rendering as an interactive
scatter plot.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from loguru import logger

from api.deps import get_exa_client, get_session_id
from services.core.exa_client import ExaClient

router = APIRouter(prefix="/api", tags=["cluster"])


@router.post("/cluster")
async def cluster_papers_endpoint(request: Request) -> JSONResponse:
    """Compute paper clusters for the current session's search results.

    Returns 2D coordinates (t-SNE) and cluster assignments for each paper,
    plus auto-generated cluster labels.
    """
    session_id = await get_session_id(request)
    exa_client = get_exa_client()

    if exa_client is None:
        return JSONResponse(
            {"ok": False, "error": "Search service not available"},
            status_code=503,
        )

    client = cast(ExaClient, exa_client)
    cached_search = client.get_cached_search(session_id)

    if cached_search is None:
        return JSONResponse(
            {"ok": False, "error": "No search results to cluster. Run a search first."},
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

    # Run clustering in thread pool (CPU-bound: embedding + t-SNE + k-means)
    from services.paper.clustering import cluster_papers

    result = await asyncio.to_thread(cluster_papers, paper_dicts)

    return JSONResponse({
        "ok": True,
        "papers": result.papers,
        "clusters": result.clusters,
    })


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
            try:
                year = str(p.published_date)[:4]
            except (TypeError, AttributeError):
                pass

        paper_dicts.append({
            "id": p.id or "",
            "title": p.title or "",
            "authors": p.authors or [],
            "year": year,
            "highlights": highlights_map.get(url_str, ""),
            "citation_count": p.citation_count,
        })

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
