"""Metadata fetchers for arXiv, Semantic Scholar, and Crossref."""

import json
import re as _re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from xml.etree.ElementTree import ParseError

from loguru import logger

# core helpers (avoid relative up-level mis-import)
from services.core.exa_utils import author_utils as _author_utils

# arXiv API ---------------------------------------------------------------
_ARXIV_NEXT_OK: float = 0.0  # polite global throttle (≈1 req / 3 s)


def fetch_arxiv_metadata(arxiv_id: str, timeout: int = 10) -> dict[str, Any]:
    """Return basic metadata for *arxiv_id* via arXiv's public Atom feed.

    The response includes ``title``, ``authors`` (list[str]) and
    ``published_date`` when available.  On any network/parsing failure an empty
    dict is returned so callers can fall back to other sources.
    """
    global _ARXIV_NEXT_OK

    now = time.time()
    if now < _ARXIV_NEXT_OK:
        time.sleep(_ARXIV_NEXT_OK - now)

    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "LuxScholarBot/0.1 (metadata enrichment)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes: bytes = resp.read()
        _ARXIV_NEXT_OK = time.time() + 3.2  # reset throttle

        root = ET.fromstring(xml_bytes)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}

        meta: dict[str, Any] = {}

        title_el = entry.find("atom:title", ns)
        if title_el is not None and title_el.text:
            meta["title"] = title_el.text.strip().replace("\n", " ")

        authors: list[str] = []
        for auth_el in entry.findall("atom:author", ns):
            nm_el = auth_el.find("atom:name", ns)
            if nm_el is not None and nm_el.text:
                nm = nm_el.text.strip()
                if nm and _author_utils.is_plausible_author(nm):
                    authors.append(nm)
        if authors:
            meta["authors"] = authors

        published_el = entry.find("atom:published", ns)
        if published_el is not None and published_el.text:
            meta["published_date"] = published_el.text.strip()

        return meta
    except (URLError, HTTPError, TimeoutError, ParseError) as exc:
        logger.debug("arXiv fetch failed for {} - {}", arxiv_id, exc)
        return {}


def fetch_arxiv_metadata_batch(arxiv_ids: list[str], timeout: int = 15) -> dict[str, dict[str, Any]]:
    """Fetch metadata for multiple arXiv IDs in a single API call.

    Returns a dict mapping arxiv_id → metadata dict (same format as
    ``fetch_arxiv_metadata``).  IDs that fail parsing are silently skipped.
    """
    global _ARXIV_NEXT_OK

    if not arxiv_ids:
        return {}

    now = time.time()
    if now < _ARXIV_NEXT_OK:
        time.sleep(_ARXIV_NEXT_OK - now)

    id_list = ",".join(arxiv_ids)
    api_url = f"https://export.arxiv.org/api/query?id_list={id_list}&max_results={len(arxiv_ids)}"
    req = urllib.request.Request(
        api_url,
        headers={"User-Agent": "LuxScholarBot/0.1 (batch metadata enrichment)"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_bytes: bytes = resp.read()
        _ARXIV_NEXT_OK = time.time() + 3.2

        root = ET.fromstring(xml_bytes)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        results: dict[str, dict[str, Any]] = {}
        for entry in root.findall("atom:entry", ns):
            # Extract arXiv ID from the entry's <id> element
            id_el = entry.find("atom:id", ns)
            if id_el is None or not id_el.text:
                continue
            # arXiv <id> looks like "http://arxiv.org/abs/2301.00001v2"
            m = _re.search(r"(\d{4}\.\d{4,5})", id_el.text)
            if not m:
                continue
            entry_id = m.group(1)

            meta: dict[str, Any] = {}
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                meta["title"] = title_el.text.strip().replace("\n", " ")

            authors: list[str] = []
            for auth_el in entry.findall("atom:author", ns):
                nm_el = auth_el.find("atom:name", ns)
                if nm_el is not None and nm_el.text:
                    nm = nm_el.text.strip()
                    if nm and _author_utils.is_plausible_author(nm):
                        authors.append(nm)
            if authors:
                meta["authors"] = authors

            published_el = entry.find("atom:published", ns)
            if published_el is not None and published_el.text:
                meta["published_date"] = published_el.text.strip()

            if meta:
                results[entry_id] = meta

        logger.info("arxiv_batch_fetch", requested=len(arxiv_ids), returned=len(results))
        return results
    except (URLError, HTTPError, TimeoutError, ParseError) as exc:
        logger.warning("arXiv batch fetch failed - {}", exc)
        return {}


# Semantic Scholar Graph API ---------------------------------------------
_SS_REQ_DAY: date = datetime.utcnow().date()
_SS_REQ_COUNT: int = 0  # ~100 anonymous requests per day allowed


def fetch_doi_metadata(doi: str, timeout: int = 10) -> dict[str, Any]:
    """Return minimal metadata for *doi* via the Semantic Scholar Graph API."""
    global _SS_REQ_DAY, _SS_REQ_COUNT

    today = datetime.utcnow().date()
    if today != _SS_REQ_DAY:
        _SS_REQ_DAY = today
        _SS_REQ_COUNT = 0
    if _SS_REQ_COUNT >= 100:
        return {}

    api_url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
        "?fields=title,authors,year"
    )
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as resp:
            if resp.status == 429:  # rate-limited
                _SS_REQ_COUNT = 100
                return {}
            data: Mapping[str, Any] = json.load(resp)
        _SS_REQ_COUNT += 1
    except (URLError, HTTPError, json.JSONDecodeError) as exc:
        logger.debug("SemanticScholar DOI fetch failed for {} - {}", doi, exc)
        return {}

    meta: dict[str, Any] = {}
    if title := data.get("title"):
        meta["title"] = title.strip()
    if auth_list := data.get("authors"):
        names = [a.get("name", "").strip() for a in auth_list if a.get("name")]
        names = [n for n in names if _author_utils.is_plausible_author(n)]
        if names:
            meta["authors"] = names
    if year := data.get("year"):
        meta["published_date"] = f"{year}-01-01"
    return meta


# Crossref REST API -------------------------------------------------------
def fetch_crossref_metadata(doi: str, timeout: int = 10) -> dict[str, Any]:
    """Return Crossref JSON metadata for *doi* (best-effort)."""
    api_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as resp:
            data: Mapping[str, Any] = json.load(resp).get("message", {})
    except (URLError, HTTPError, json.JSONDecodeError) as exc:
        logger.debug("Crossref fetch failed for {} - {}", doi, exc)
        return {}

    meta: dict[str, Any] = {}
    if title_list := data.get("title"):
        meta["title"] = title_list[0]
    if auth_list := data.get("author"):
        names: list[str] = []
        for a in auth_list:
            full = f"{a.get('given', '').strip()} {a.get('family', '').strip()}".strip()
            if full and _author_utils.is_plausible_author(full):
                names.append(full)
        if names:
            meta["authors"] = names
    if (issued := data.get("issued", {})).get("date-parts"):
        parts = issued["date-parts"][0]
        year, month, day = (
            parts[0],
            parts[1] if len(parts) > 1 else 1,
            parts[2] if len(parts) > 2 else 1,
        )
        meta["published_date"] = f"{year:04d}-{month:02d}-{day:02d}"
    if abstract := data.get("abstract"):
        meta["text_content"] = _re.sub(r"<[^>]+>", "", abstract).strip()
    return meta


# Semantic Scholar arXiv endpoint (placeholder) ---------------------------
def fetch_semantic_scholar_arxiv(arxiv_id: str, timeout: int = 10) -> dict[str, Any]:
    """Return empty dict – placeholder until Semantic Scholar offers an endpoint."""
    return {}
