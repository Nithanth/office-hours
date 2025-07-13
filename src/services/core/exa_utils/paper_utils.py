"""Stateless helpers for transforming and validating `Paper` objects."""

import re
from contextlib import suppress
from datetime import datetime, timezone
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


def create_paper_from_exa_result(result: Any, source_description: str) -> Optional[Paper]:
    """Convert a raw Exa SDK `result` into a `Paper` object."""
    try:
        title_val: str = getattr(result, "title", None) or "No Title Provided"
        raw_url: str = getattr(result, "url", "")
        url_val: str = _url_utils.upgrade_to_pdf_url(raw_url) or raw_url

        raw_authors_any = getattr(result, "authors", None) or getattr(result, "author", None)
        authors_list: list[str] = []
        if authors_attr := getattr(result, "authors", None):
            authors_list = [str(a).strip() for a in authors_attr if str(a).strip()]
        elif author_str := getattr(result, "author", None):
            tokens = re.split(r",|;|\band\b", str(author_str))
            authors_list = [t.strip() for t in tokens if _author_utils.is_plausible_author(t)]

        logger.debug("create_paper_from_exa_result", raw_authors=raw_authors_any, kept=authors_list)

        published_date: datetime | None = None
        if pd_str := getattr(result, "published_date", None):
            with suppress(Exception):
                if "T" in pd_str:
                    published_date = datetime.fromisoformat(pd_str.replace("Z", "+00:00"))
                else:
                    published_date = datetime.strptime(pd_str, "%Y-%m-%d").replace(tzinfo=None)

        paper = Paper(
            id=getattr(result, "id", None),
            title=title_val,
            url=url_val,
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

        if not existing_ok:
            paper.authors = clean_authors

    if meta.get("published_date") and not paper.published_date:
        with suppress(Exception):
            paper.published_date = datetime.fromisoformat(str(meta["published_date"])).replace(
                tzinfo=timezone.utc
            )

    if meta.get("text_content") and not paper.text_content:
        paper.text_content = meta["text_content"]

    return paper
