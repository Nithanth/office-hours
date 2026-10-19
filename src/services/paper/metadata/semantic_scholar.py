"""Semantic Scholar citation count enrichment.

Lightweight client that batch-fetches citation counts for papers using
the S2 Graph API batch endpoint. Designed to be called post-search to
enrich papers with citation data for ranking and visualization.

API docs: https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Optional
from urllib.error import HTTPError, URLError

from loguru import logger

import config as _cfg

# Rate limiting (S2 free tier: 100 requests/5 minutes)
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_BATCH_LIMIT = 500  # S2 batch endpoint max

_ARXIV_ABS_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([\d.]+)(?:v\d+)?")
_DOI_URL_RE = re.compile(r"doi\.org/([^\s?#]+)")


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
    meta = batch_fetch_paper_meta(paper_ids, timeout=timeout)
    for pid, m in meta.items():
        if m.get("citations") is not None:
            results[pid] = int(m["citations"])
    return results


def batch_fetch_paper_meta(
    paper_ids: list[str],
    *,
    timeout: int = 10,
) -> dict[str, dict[str, Any]]:
    """Batch-fetch citation counts + externalIds (arXiv/DOI) for papers.

    Same response and ID scheme as ``batch_fetch_citations``, but keeps the
    ``externalIds`` payload — the arXiv mapping lets the pipeline rewrite
    paywalled publisher links (dl.acm.org etc.) to the open-access arXiv
    version so the viewer and text extractor always have a readable stream.

    Returns
    -------
    dict[str, dict]
        Mapping of input paper_id → {"citations": int|None, "arxiv_id":
        str|None, "doi": str|None}. Papers not found are omitted.
    """
    if not paper_ids:
        return {}

    results: dict[str, dict[str, Any]] = {}
    for i in range(0, len(paper_ids), _BATCH_LIMIT):
        batch = paper_ids[i : i + _BATCH_LIMIT]
        results.update(_fetch_batch(batch, timeout=timeout))
    return results


def _fetch_batch(ids: list[str], timeout: int = 10) -> dict[str, dict[str, Any]]:
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

    results: dict[str, dict[str, Any]] = {}
    for i, item in enumerate(data or []):
        if item is None or i >= len(ids):
            continue
        ext = item.get("externalIds") or {}
        results[ids[i]] = {
            "citations": item.get("citationCount"),
            "arxiv_id": ext.get("ArXiv"),
            "doi": ext.get("DOI"),
        }

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


def _s2_identifier_from_url(url: str) -> Optional[str]:
    """Map a paper URL to an S2 API identifier (arXiv / DOI / URL fallback)."""
    if not url:
        return None
    m = _ARXIV_ABS_RE.search(url)
    if m:
        return f"arXiv:{m.group(1)}"
    m = _DOI_URL_RE.search(url)
    if m:
        return f"DOI:{m.group(1)}"
    if "semanticscholar.org" in url:
        return f"URL:{url}"
    return None


def fetch_paper_references(
    paper_url: str,
    *,
    limit: int = 40,
    min_citations: int = 300,
    timeout: int = 10,
) -> list[Any]:
    """Top-cited references of a paper — its intellectual ancestors.

    Used by the canonical merge's citation-graph expansion: a seminal hit's
    own bibliography (SIREN → DeepSDF, Occupancy Networks, NeRF) is the
    field canon that keyword search misses. Returns Paper objects sorted
    by citation count desc; empty on failure.
    """
    from models.paper import Paper

    ident = _s2_identifier_from_url(paper_url)
    if ident is None:
        return []

    params = urllib.parse.urlencode(
        {
            "limit": limit,
            "fields": "title,authors,year,citationCount,externalIds,url,"
            "abstract,publicationDate",
        }
    )
    req = urllib.request.Request(
        f"{_S2_BASE}/paper/{urllib.parse.quote(ident, safe='')}/references?{params}",
        headers=_get_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("s2_references_fetch_failed", ident=ident, err=str(exc))
        return []

    papers: list[Any] = []
    for edge in data.get("data") or []:
        item = edge.get("citedPaper") or {}
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


# Hosts that reliably serve the paper itself (open access). When S2 says a
# result also lives on arXiv, we only rewrite the URL when the current host
# is NOT in this set — an ACL Anthology or OpenReview link is already a good
# readable stream.
_OPEN_ACCESS_HOSTS = (
    "arxiv.org",
    "ar5iv.org",
    "aclanthology.org",
    "openreview.net",
    "biorxiv.org",
    "medrxiv.org",
    "proceedings.mlr.press",
    "jmlr.org",
    "papers.nips.cc",
    "neurips.cc",
    "usenix.org",
    "ojs.aaai.org",
)


def _is_open_access_url(url_str: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url_str).netloc.lower()
    return any(host == h or host.endswith(f".{h}") for h in _OPEN_ACCESS_HOSTS)


def enrich_papers_with_citations(
    papers: list[Any],
    *,
    timeout: int = 10,
) -> None:
    """Enrich a list of Paper objects with citation counts (in-place).

    Attempts to resolve each paper via arXiv ID first, then DOI, then URL.

    Also canonicalizes URLs: when S2's externalIds show an arXiv version for
    a result whose link points at a non-open-access host (dl.acm.org, IEEE,
    Elsevier…), the paper's URL is rewritten to ``arxiv.org/abs/<id>`` so the
    display link, the viewer's ar5iv/PDF path, and the text extractor's arXiv
    layer all hit the reliably-readable stream instead of a paywall. Papers
    already on an open-access host keep their URL.
    """
    from pydantic import HttpUrl

    from services.core.exa_utils import url_utils as _url_utils

    # Build ID list for S2 batch API
    id_map: dict[str, list[Any]] = {}  # s2_id → [paper, ...]
    for paper in papers:
        url_str = str(paper.url) if paper.url else ""

        # Try arXiv ID first (most reliable) — but only when the URL is
        # actually on an arXiv host. extract_arxiv_id's loose \d{4}.\d{4,5}
        # pattern false-positives inside DOIs (10.1145/3447548.3467171
        # contains "7548.34671"), which would resolve ACM links to a bogus
        # ARXIV id and silently lose their citation/externalIds mapping.
        url_lc = url_str.lower()
        if "arxiv.org" in url_lc or "ar5iv.org" in url_lc:
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

    # Batch fetch (citations + externalIds in one response)
    meta = batch_fetch_paper_meta(list(id_map.keys()), timeout=timeout)

    # Apply results
    enriched = 0
    canonicalized = 0
    for s2_id, m in meta.items():
        for paper in id_map.get(s2_id, []):
            if m.get("citations") is not None:
                paper.citation_count = m["citations"]
                enriched += 1
            # Rewrite paywalled/publisher links to the arXiv version S2 maps
            # this paper to — the arXiv abs page always loads and the text
            # extractor/viewer both have a guaranteed readable stream there.
            arxiv_id = m.get("arxiv_id")
            if arxiv_id and paper.url:
                old_url = str(paper.url)
                if not _is_open_access_url(old_url):
                    new_url = f"https://arxiv.org/abs/{arxiv_id}"
                    paper.url = HttpUrl(new_url)
                    if str(paper.id) == old_url:
                        paper.id = new_url
                    canonicalized += 1
                    logger.debug(
                        "paper_url_canonicalized", from_url=old_url, to_url=new_url
                    )

    # Open-access flagging + Unpaywall rescue ---------------------------
    # Every paper gets its is_open_access flag; ones still on closed hosts
    # get a Unpaywall lookup for a legit open copy (repository, PMC, OA
    # publisher mirror) before being marked closed.
    closed: list[Any] = []
    for paper in papers:
        if not paper.url:
            continue
        if _is_open_access_url(str(paper.url)):
            paper.is_open_access = True
        else:
            closed.append(paper)

    unpaywalled = 0
    if closed:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from services.paper.metadata.unpaywall import resolve_open_access_url

        def _doi_for(paper: Any) -> str | None:
            # Prefer the DOI S2 resolved for this paper (exact record),
            # fall back to one embedded in the URL itself.
            for s2_id, m in meta.items():
                doi = m.get("doi")
                if paper in id_map.get(s2_id, []) and isinstance(doi, str):
                    return doi
            result = _url_utils.extract_doi(str(paper.url))
            return result if isinstance(result, str) else None

        def _resolve(paper: Any) -> str | None:
            doi = _doi_for(paper)
            return (
                resolve_open_access_url(doi, timeout=min(timeout, 6)) if doi else None
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_map = {pool.submit(_resolve, p): p for p in closed}
            for fut in as_completed(fut_map):
                paper = fut_map[fut]
                try:
                    oa_url = fut.result()
                except Exception as exc:  # noqa: BLE001 – best-effort
                    logger.debug(
                        "unpaywall_resolve_failed",
                        url=str(paper.url),
                        err=str(exc),
                    )
                    oa_url = None
                if oa_url:
                    old_url = str(paper.url)
                    paper.url = HttpUrl(oa_url)
                    if str(paper.id) == old_url:
                        paper.id = oa_url
                    paper.is_open_access = True
                    unpaywalled += 1
                    canonicalized += 1
                    logger.debug(
                        "paper_url_unpaywalled", from_url=old_url, to_url=oa_url
                    )
                else:
                    paper.is_open_access = False

    if unpaywalled:
        logger.info("unpaywall_rescued", count=unpaywalled, closed=len(closed))

    # Re-dedup: canonicalization can collapse an ACM entry and its arXiv
    # twin that slipped through the earlier URL-based dedup. Keep the first
    # occurrence of each normalized key.
    if canonicalized:
        seen: set[str] = set()
        deduped: list[Any] = []
        for paper in papers:
            key = (
                _url_utils.extract_arxiv_id(str(paper.url))
                or _url_utils.extract_doi(str(paper.url))
                or str(paper.url)
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(paper)
        if len(deduped) < len(papers):
            removed = len(papers) - len(deduped)
            papers[:] = deduped
            logger.info("s2_canonical_dedup", removed=removed)

    if meta or not id_map:
        logger.info(
            "s2_citation_enrichment",
            total_papers=len(papers),
            resolved=len(meta),
            enriched=enriched,
            canonicalized=canonicalized,
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
