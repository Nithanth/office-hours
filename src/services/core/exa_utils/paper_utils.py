"""Stateless helpers for transforming and validating `Paper` objects."""

import re
from contextlib import suppress
from datetime import datetime
from typing import Any, Optional, cast

from loguru import logger

from . import author_utils as _author_utils
from . import url_utils as _url_utils

try:
    from models.paper import Paper
except ImportError:  # pragma: no cover – fallback when src/ not on PYTHONPATH
    import os
    import sys

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if PROJECT_ROOT not in sys.path:
        sys.path.append(PROJECT_ROOT)
    from models.paper import Paper

from pydantic import HttpUrl, parse_obj_as

_PDF_TITLE_PATTERNS = [
    re.compile(r"\s*(?:-|\||:)\s*pdf\s*$", re.IGNORECASE),
    re.compile(r"\s*\[?\(?pdf\)?\]?\s*$", re.IGNORECASE),
    re.compile(r"\.pdf\s*$", re.IGNORECASE),
    re.compile(r"\s*-\s*arxiv\s*$", re.IGNORECASE),
]

# Leading patterns (e.g., "[2502.09992] " or "[123456] ") to remove
_ARXIV_PREFIX_PATTERN = re.compile(r"^\s*\[?(\d{4}\.\d{4,5})(?:v\d+)?\]?\s*", re.IGNORECASE)


def _clean_title(raw_title: str | None) -> str:
    """Return *raw_title* cleaned of common noise (PDF, arXiv ID, etc.)."""
    if not raw_title:
        return ""

    title = str(raw_title).strip()
    # Remove leading arXiv identifier like "[2502.09992] "
    title = _ARXIV_PREFIX_PATTERN.sub("", title)
    for pat in _PDF_TITLE_PATTERNS:
        title = pat.sub("", title)
    return title.strip()


def is_valid_paper(paper: Paper | None) -> bool:
    """Heuristic sanity-check for *paper* metadata."""
    if paper is None:
        return False

    title_clean = (paper.title or "").strip()
    if not title_clean or title_clean == "No Title Provided":
        return False
    if len(title_clean) < 8:
        return False
    blacklist = {"recaptcha", "captcha", "not found", "404", "error"}
    if any(bad in title_clean.lower() for bad in blacklist):
        return False
    if not paper.url:
        return False

    return bool(
        (paper.authors and len(paper.authors) > 0)
        or (paper.text_content and len(paper.text_content) >= 100)
    )


def _coerce_http_url(raw: str) -> HttpUrl | str:
    """Ensure *raw* is an absolute HTTP(S) URL and return a validated HttpUrl.

    When validation fails, the original string is returned and a debug log is
    emitted. This centralises URL normalisation so every Paper instance sees
    consistent, Pydantic-friendly values.
    """
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    try:
        return parse_obj_as(HttpUrl, raw)
    except Exception as exc:  # noqa: BLE001 – keep raw str on failure
        logger.debug("url_validation_failed", raw=raw, err=str(exc))
        return raw


def create_paper_from_exa_result(result: Any, source_description: str) -> Optional[Paper]:
    """Convert a raw Exa SDK `result` into a `Paper` object."""
    try:
        raw_title: str | None = getattr(result, "title", None)
        title_val: str = _clean_title(raw_title) or "No Title Provided"
        raw_url: str = getattr(result, "url", "")
        url_val: str = _url_utils.upgrade_to_pdf_url(raw_url) or raw_url

        url_val_typed = _coerce_http_url(url_val)

        raw_authors_any = getattr(result, "authors", None) or getattr(result, "author", None)
        authors_list: list[str] = []
        if authors_attr := getattr(result, "authors", None):
            # Exa sometimes returns garbage strings like "Senior Author"; clean.
            _authors_cleaned = [str(a).strip() for a in authors_attr if str(a).strip()]
            authors_list = _author_utils.filter_plausible_authors(_authors_cleaned)
        elif author_str := getattr(result, "author", None):
            tokens = re.split(r",|;|\band\b", str(author_str))
            authors_list = [t.strip() for t in tokens if _author_utils.is_plausible_author(t)]

        logger.debug("create_paper_from_exa_result", raw_authors=raw_authors_any, kept=authors_list)

        published_date: datetime | None = None
        if pd_str := getattr(result, "published_date", None):
            with suppress(Exception):
                if isinstance(pd_str, str):
                    # Normalise common timestamp strings emitted by APIs (both ISO and space-separated)
                    cleaned = pd_str.replace("Z", "+00:00")
                    try:
                        published_date = datetime.fromisoformat(cleaned)
                    except ValueError:
                        # Fallback to space-separated "YYYY-MM-DD HH:MM:SS+00:00"
                        try:
                            if "+" in cleaned:
                                dt_part, tz_part = cleaned.split("+", 1)
                                paper_dt = datetime.strptime(dt_part.strip(), "%Y-%m-%d %H:%M:%S")
                                published_date = paper_dt.replace(tzinfo=None)
                            else:
                                # Bare date string
                                published_date = datetime.strptime(cleaned, "%Y-%m-%d").replace(
                                    tzinfo=None
                                )
                        except Exception:
                            published_date = None
                else:
                    published_date = pd_str  # assume datetime already

        paper = Paper(
            id=getattr(result, "id", None),
            title=title_val,
            url=url_val_typed,
            authors=_author_utils.filter_plausible_authors(authors_list) or None,
            abstract=getattr(result, "abstract", None),
            published_date=published_date,
            source=source_description,
            score=getattr(result, "score", None),
            text_content=getattr(result, "text", None),
        )
        return paper
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_paper_from_exa_result_failed", result=result, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Metadata merge helper formerly on ExaClient
# ---------------------------------------------------------------------------


def update_paper_from_meta(paper: Paper, meta: dict[str, Any]) -> Paper:
    """Overlay non-empty *meta* fields onto *paper* in-place and return it."""
    if meta.get("title") and (not paper.title or paper.title == "No Title Provided"):
        paper.title = str(meta["title"])

    if meta.get("authors"):
        clean_authors = _author_utils.filter_plausible_authors(cast(list[str], meta["authors"]))
        logger.debug(
            "update_paper_from_meta",
            incoming=meta.get("authors"),
            clean=clean_authors,
            existing=paper.authors,
        )

        existing_ok = False
        if paper.authors:
            any_full_name = any(" " in tok for tok in paper.authors)
            if (
                any_full_name
                and any(_author_utils.is_plausible_author(tok) for tok in paper.authors)
                and len(paper.authors) >= max(3, len(clean_authors) - 1)
            ):
                existing_ok = True

        # Only replace when we actually obtained at least one plausible author;
        # prevents wiping out a valid list with an empty fallback.
        if clean_authors and not existing_ok:
            paper.authors = clean_authors

    if meta.get("published_date") and not paper.published_date:
        pd_raw = meta["published_date"]
        try:
            if isinstance(pd_raw, str):
                # Normalise common timestamp strings emitted by APIs (both ISO and space-separated)
                cleaned = pd_raw.replace("Z", "+00:00")
                try:
                    paper.published_date = datetime.fromisoformat(cleaned)
                except ValueError:
                    # Fallback to space-separated "YYYY-MM-DD HH:MM:SS+00:00"
                    try:
                        if "+" in cleaned:
                            dt_part, tz_part = cleaned.split("+", 1)
                            paper_dt = datetime.strptime(dt_part.strip(), "%Y-%m-%d %H:%M:%S")
                            paper.published_date = paper_dt.replace(tzinfo=None)
                        else:
                            # Bare date string
                            paper.published_date = datetime.strptime(cleaned, "%Y-%m-%d").replace(
                                tzinfo=None
                            )
                    except Exception:
                        paper.published_date = None
            else:
                paper.published_date = pd_raw  # assume datetime already
        except Exception:  # noqa: BLE001 – defensive, keep original string
            paper.published_date = pd_raw

    if meta.get("text_content") and not paper.text_content:
        paper.text_content = meta["text_content"]

    return paper
