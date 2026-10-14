"""Semantic Scholar author search and paper retrieval.

Uses the S2 Graph API to resolve author names to disambiguated entities
and fetch their publication lists.  Designed for the "search by author"
workflow where an investor or reader wants to explore a researcher's
body of work.

API docs: https://api.semanticscholar.org/api-docs/graph#tag/Author-Data
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

from loguru import logger
from pydantic import BaseModel, Field

import config as _cfg

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_OA_BASE = "https://api.openalex.org"

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class AuthorResult(BaseModel):
    """A single author returned by S2 author search."""

    author_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    url: str | None = None
    paper_count: int = 0
    citation_count: int = 0
    h_index: int = 0


class AuthorPaper(BaseModel):
    """A paper from an author's publication list."""

    paper_id: str
    title: str
    year: int | None = None
    citation_count: int = 0
    venue: str | None = None
    url: str | None = None
    abstract: str | None = None
    external_ids: dict[str, Any] = Field(default_factory=dict)


class AuthorProfile(BaseModel):
    """Full author profile combining identity + papers."""

    author: AuthorResult
    papers: list[AuthorPaper] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


class S2UnavailableError(RuntimeError):
    """Semantic Scholar request failed (rate limit / network) — distinct from
    a genuine zero-result response."""


_OA_ID_RE = re.compile(r"^A\d{5,}$")


def _oa_get(
    path: str, params: dict[str, str] | None = None, *, timeout: int = 10
) -> Any:
    """GET the OpenAlex API — keyless fallback for author search when S2 429s.

    Free keys exist (instant, no approval); set OPENALEX_API_KEY to get a
    dedicated budget instead of the per-IP shared one."""
    url = f"{_OA_BASE}{path}"
    params = dict(params or {})
    api_key = getattr(_cfg.config, "OPENALEX_API_KEY", None)
    if api_key:
        params["api_key"] = api_key
    mailto = getattr(_cfg.config, "OPENALEX_MAILTO", None)
    if mailto:
        params["mailto"] = mailto
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers=_get_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("openalex_api_request_failed", path=path, err=str(exc))
        raise


def _oa_author(item: dict[str, Any]) -> AuthorResult:
    """Map an OpenAlex author record onto AuthorResult."""
    stats = item.get("summary_stats") or {}
    oid = (item.get("id") or "").rsplit("/", 1)[-1]
    return AuthorResult(
        author_id=oid,
        name=item.get("display_name") or "Unknown",
        aliases=item.get("display_name_alternatives") or [],
        url=item.get("id"),
        paper_count=item.get("works_count") or 0,
        citation_count=item.get("cited_by_count") or 0,
        h_index=stats.get("h_index") or 0,
    )


def _oa_abstract(inv: dict[str, Any] | None) -> str | None:
    """Reconstruct an abstract from OpenAlex's inverted index."""
    if not inv:
        return None
    size = max(pos for positions in inv.values() for pos in positions) + 1
    words = [""] * size
    for word, positions in inv.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words)


def _get_headers() -> dict[str, str]:
    """Build request headers, including API key if configured."""
    headers: dict[str, str] = {
        "User-Agent": "OfficeHours/1.0",
        "Content-Type": "application/json",
    }
    api_key = getattr(_cfg.config, "SEMANTIC_SCHOLAR_API_KEY", None)
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _s2_get(
    path: str, params: dict[str, str] | None = None, *, timeout: int = 10
) -> Any:
    """Make a GET request to the S2 Graph API."""
    url = f"{_S2_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers=_get_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.warning("s2_api_request_failed", path=path, err=str(exc))
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_authors(
    query: str,
    *,
    limit: int = 5,
    timeout: int = 10,
) -> list[AuthorResult]:
    """Search for authors by name.

    Parameters
    ----------
    query
        Author name (e.g. "Yann LeCun", "Y. Bengio").
    limit
        Maximum results (1-1000).
    timeout
        HTTP timeout in seconds.

    Returns
    -------
    list[AuthorResult]
        Matched authors sorted by relevance.
    """
    if not query or not query.strip():
        return []

    fields = "name,aliases,url,paperCount,citationCount,hIndex"
    try:
        data = _s2_get(
            "/author/search",
            params={
                "query": query.strip(),
                "fields": fields,
                "limit": str(min(limit, 1000)),
            },
            timeout=timeout,
        )
    except Exception as exc:
        if isinstance(exc, HTTPError) and exc.code == 429:
            logger.warning("s2_rate_limited", query=query)
        try:
            oa = _oa_get(
                "/authors",
                params={"search": query.strip(), "per-page": str(min(limit, 50))},
                timeout=timeout,
            )
            oa_results = [_oa_author(i) for i in oa.get("results", [])]
            logger.info(
                "openalex_author_search_fallback",
                query=query,
                returned=len(oa_results),
            )
            return oa_results
        except Exception:
            raise S2UnavailableError(str(exc)) from exc

    results: list[AuthorResult] = []
    for item in data.get("data", []):
        results.append(
            AuthorResult(
                author_id=str(item.get("authorId", "")),
                name=item.get("name", "Unknown"),
                aliases=item.get("aliases") or [],
                url=item.get("url"),
                paper_count=item.get("paperCount", 0) or 0,
                citation_count=item.get("citationCount", 0) or 0,
                h_index=item.get("hIndex", 0) or 0,
            )
        )

    logger.info(
        "s2_author_search",
        query=query,
        total=data.get("total", 0),
        returned=len(results),
    )
    return results


def get_author_papers(
    author_id: str,
    *,
    limit: int = 50,
    timeout: int = 15,
) -> list[AuthorPaper]:
    """Fetch papers for a given author ID.

    Parameters
    ----------
    author_id
        Semantic Scholar author ID (numeric string).
    limit
        Maximum papers to fetch (1-1000).
    timeout
        HTTP timeout in seconds.

    Returns
    -------
    list[AuthorPaper]
        Papers sorted by citation count (descending).
    """
    if _OA_ID_RE.match(author_id):
        return _oa_author_papers(author_id, limit=limit, timeout=timeout)

    fields = "title,year,citationCount,venue,url,abstract,externalIds"
    try:
        data = _s2_get(
            f"/author/{urllib.parse.quote(author_id)}/papers",
            params={"fields": fields, "limit": str(min(limit, 1000))},
            timeout=timeout,
        )
    except Exception as exc:
        if isinstance(exc, HTTPError) and exc.code == 429:
            logger.warning("s2_rate_limited", author_id=author_id)
        raise S2UnavailableError(str(exc)) from exc

    papers: list[AuthorPaper] = []
    for item in data.get("data", []):
        papers.append(
            AuthorPaper(
                paper_id=item.get("paperId", ""),
                title=item.get("title", "Untitled"),
                year=item.get("year"),
                citation_count=item.get("citationCount", 0) or 0,
                venue=item.get("venue") or None,
                url=item.get("url"),
                abstract=item.get("abstract"),
                external_ids=item.get("externalIds") or {},
            )
        )

    # Sort by citation count descending (most impactful first)
    papers.sort(key=lambda p: p.citation_count, reverse=True)
    logger.info("s2_author_papers", author_id=author_id, returned=len(papers))
    return papers


def get_author_profile(
    author_id: str,
    *,
    paper_limit: int = 30,
    timeout: int = 15,
) -> AuthorProfile | None:
    """Fetch full author profile: identity + top papers.

    Returns None if the author is not found.
    """
    # Fetch author details
    fields = "name,aliases,url,paperCount,citationCount,hIndex"
    if _OA_ID_RE.match(author_id):
        return _oa_author_profile(author_id, paper_limit=paper_limit, timeout=timeout)
    try:
        data = _s2_get(
            f"/author/{urllib.parse.quote(author_id)}",
            params={"fields": fields},
            timeout=timeout,
        )
    except HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 429:
            logger.warning("s2_rate_limited", author_id=author_id)
        raise S2UnavailableError(str(exc)) from exc
    except Exception as exc:
        raise S2UnavailableError(str(exc)) from exc

    author = AuthorResult(
        author_id=str(data.get("authorId", author_id)),
        name=data.get("name", "Unknown"),
        aliases=data.get("aliases") or [],
        url=data.get("url"),
        paper_count=data.get("paperCount", 0) or 0,
        citation_count=data.get("citationCount", 0) or 0,
        h_index=data.get("hIndex", 0) or 0,
    )

    try:
        papers = get_author_papers(author_id, limit=paper_limit, timeout=timeout)
    except S2UnavailableError:
        # Degrade to an empty paper list rather than failing the whole profile.
        papers = []

    return AuthorProfile(author=author, papers=papers)


def _oa_author_papers(
    author_id: str, *, limit: int = 50, timeout: int = 15
) -> list[AuthorPaper]:
    """Fetch an author's works from OpenAlex (author_id like 'A5023888391')."""
    try:
        data = _oa_get(
            "/works",
            params={
                "filter": f"author.id:{author_id}",
                "sort": "cited_by_count:desc",
                "per-page": str(min(limit, 200)),
            },
            timeout=timeout,
        )
    except Exception as exc:
        raise S2UnavailableError(str(exc)) from exc

    papers: list[AuthorPaper] = []
    for item in data.get("results", []):
        ids = item.get("ids") or {}
        ext: dict[str, Any] = {}
        if ids.get("doi"):
            ext["DOI"] = str(ids["doi"]).removeprefix("https://doi.org/")
        primary = (item.get("primary_location") or {}).get("source") or {}
        papers.append(
            AuthorPaper(
                paper_id=(item.get("id") or "").rsplit("/", 1)[-1],
                title=item.get("display_name") or "Untitled",
                year=item.get("publication_year"),
                citation_count=item.get("cited_by_count") or 0,
                venue=primary.get("display_name") or None,
                url=ids.get("openalex") or ids.get("doi"),
                abstract=_oa_abstract(item.get("abstract_inverted_index")),
                external_ids=ext,
            )
        )
    logger.info("openalex_author_papers", author_id=author_id, returned=len(papers))
    return papers


def _oa_author_profile(
    author_id: str, *, paper_limit: int = 30, timeout: int = 15
) -> AuthorProfile | None:
    """Fetch an OpenAlex author + their top works."""
    try:
        data = _oa_get(f"/authors/{urllib.parse.quote(author_id)}", timeout=timeout)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise S2UnavailableError(str(exc)) from exc
    except Exception as exc:
        raise S2UnavailableError(str(exc)) from exc

    author = _oa_author(data)
    try:
        papers = _oa_author_papers(author_id, limit=paper_limit, timeout=timeout)
    except S2UnavailableError:
        papers = []
    return AuthorProfile(author=author, papers=papers)


def get_paper_url(paper: AuthorPaper) -> str | None:
    """Derive the best URL for a paper from its external IDs.

    Prefers arXiv > DOI > S2 URL > paper.url field.
    """
    ext = paper.external_ids

    # arXiv
    if arxiv_id := ext.get("ArXiv"):
        return f"https://arxiv.org/abs/{arxiv_id}"

    # DOI
    if doi := ext.get("DOI"):
        return f"https://doi.org/{doi}"

    # Semantic Scholar URL
    if paper.url:
        return paper.url

    # S2 paper page
    if paper.paper_id:
        return f"https://www.semanticscholar.org/paper/{paper.paper_id}"

    return None
