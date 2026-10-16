"""Semantic Scholar citation count enrichment.

Lightweight client that batch-fetches citation counts for papers using
the S2 Graph API batch endpoint. Designed to be called post-search to
enrich papers with citation data for ranking and visualization.

API docs: https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

from loguru import logger

import config as _cfg

# Rate limiting (S2 free tier: 100 requests/5 minutes)
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_BATCH_LIMIT = 500  # S2 batch endpoint max


def _get_headers() -> dict[str, str]:
    """Build request headers, including API key if configured."""
    headers = {"User-Agent": "OfficeHours/1.0", "Content-Type": "application/json"}
    api_key = getattr(_cfg.config, "SEMANTIC_SCHOLAR_API_KEY", None)
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def batch_fetch_citations(
    paper_ids: list[str],
    *,
    timeout: int = 10,
) -> dict[str, int]:
    """Batch-fetch citation counts for papers.

    Parameters
    ----------
    paper_ids
        List of S2-compatible paper identifiers. Supports:
        - ``"ARXIV:<arxiv_id>"`` (e.g., "ARXIV:2106.09685")
        - ``"DOI:<doi>"`` (e.g., "DOI:10.1234/...")
        - ``"URL:<url>"`` (e.g., "URL:https://arxiv.org/abs/...")
        - Raw S2 paper IDs (40-char hex)
    timeout
        Request timeout in seconds.

    Returns
    -------
    dict[str, int]
        Mapping of input paper_id → citation count.
        Papers not found are omitted from the result.
    """
    if not paper_ids:
        return {}

    results: dict[str, int] = {}

    # Process in batches of 500 (S2 limit)
    for i in range(0, len(paper_ids), _BATCH_LIMIT):
        batch = paper_ids[i : i + _BATCH_LIMIT]
        batch_results = _fetch_batch(batch, timeout=timeout)
        results.update(batch_results)

    return results


def _fetch_batch(ids: list[str], timeout: int = 10) -> dict[str, int]:
    """Single batch API call."""
    url = f"{_S2_BASE}/paper/batch?fields=citationCount,externalIds"
    payload = json.dumps({"ids": ids}).encode()

    req = urllib.request.Request(
        url, data=payload, headers=_get_headers(), method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("s2_batch_citation_fetch_failed", n_ids=len(ids), err=str(exc))
        return {}

    results: dict[str, int] = {}
    for i, item in enumerate(data or []):
        if item is None:
            continue
        citation_count = item.get("citationCount")
        if citation_count is not None and i < len(ids):
            results[ids[i]] = int(citation_count)

    logger.debug("s2_citations_fetched", requested=len(ids), found=len(results))
    return results


def fetch_top_cited_papers(
    query: str,
    *,
    limit: int = 20,
    min_citations: int = 50,
    timeout: int = 10,
) -> list[Any]:
    """Search S2 for the most-cited papers matching *query*.

    Used to expand the Exa result pool with seminal papers Exa may have
    missed (e.g. "Attention Is All You Need" for a transformer query).
    Returns Paper objects sorted by citation count desc; empty on failure.
    """
    from models.paper import Paper

    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": limit,
            "fields": "title,authors,year,citationCount,externalIds,url,"
            "abstract,publicationDate",
        }
    )
    req = urllib.request.Request(
        f"{_S2_BASE}/paper/search?{params}", headers=_get_headers()
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("s2_top_cited_fetch_failed", query=query[:60], err=str(exc))
        return []

    papers: list[Any] = []
    for item in data.get("data") or []:
        ext = item.get("externalIds") or {}
        arxiv_id = ext.get("ArXiv")
        doi = ext.get("DOI")
        if arxiv_id:
            url = f"https://arxiv.org/abs/{arxiv_id}"
        elif doi:
            url = f"https://doi.org/{doi}"
        elif item.get("url"):
            url = item["url"]
        else:
            continue

        count = item.get("citationCount") or 0
        if count < min_citations:
            continue

        try:
            papers.append(
                Paper(
                    id=url,
                    title=item.get("title") or "Untitled",
                    url=url,
                    authors=[
                        a.get("name", "")
                        for a in (item.get("authors") or [])
                        if a.get("name")
                    ],
                    text_content=item.get("abstract"),
                    published_date=item.get("publicationDate")
                    or (f"{item['year']}-01-01" if item.get("year") else None),
                    citation_count=count,
                    source="Semantic Scholar",
                )
            )
        except Exception:
            continue

    papers.sort(key=lambda p: p.citation_count or 0, reverse=True)
    return papers


def enrich_papers_with_citations(
    papers: list[Any],
    *,
    timeout: int = 10,
) -> None:
    """Enrich a list of Paper objects with citation counts (in-place).

    Attempts to resolve each paper via arXiv ID first, then DOI, then URL.
    """
    from services.core.exa_utils import url_utils as _url_utils

    # Build ID list for S2 batch API
    id_map: dict[str, list[Any]] = {}  # s2_id → [paper, ...]
    for paper in papers:
        url_str = str(paper.url) if paper.url else ""

        # Try arXiv ID first (most reliable)
        arxiv_id = _url_utils.extract_arxiv_id(url_str)
        if arxiv_id:
            s2_id = f"ARXIV:{arxiv_id}"
            id_map.setdefault(s2_id, []).append(paper)
            continue

        # Try DOI
        doi = (
            _url_utils.extract_doi(url_str)
            if hasattr(_url_utils, "extract_doi")
            else None
        )
        if doi:
            s2_id = f"DOI:{doi}"
            id_map.setdefault(s2_id, []).append(paper)
            continue

        # Fall back to URL
        if url_str:
            s2_id = f"URL:{url_str}"
            id_map.setdefault(s2_id, []).append(paper)

    if not id_map:
        return

    # Batch fetch
    citations = batch_fetch_citations(list(id_map.keys()), timeout=timeout)

    # Apply results
    enriched = 0
    for s2_id, count in citations.items():
        for paper in id_map.get(s2_id, []):
            paper.citation_count = count
            enriched += 1

    if citations or not id_map:
        logger.info(
            "s2_citation_enrichment",
            total_papers=len(papers),
            resolved=len(citations),
            enriched=enriched,
        )
    else:
        # Zero resolved = silent citation-blind ranking (batch failure,
        # rate limit) — the composite falls back to pure rank order and a
        # 1-citation paper can top the list. Warn so prod logs show it.
        logger.warning(
            "s2_citation_enrichment_empty",
            total_papers=len(papers),
            attempted_ids=len(id_map),
        )
