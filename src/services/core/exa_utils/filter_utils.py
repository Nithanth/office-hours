"""Adaptive paper-filtering helpers (stateless).

This module contains the quality and deduplication pipeline formerly
implemented inside `ExaClient`.  Keeping it here makes the client
lighter and allows unit-testing without network or cache access.
"""

import re
from collections.abc import Iterable
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from models.paper import Paper

from . import paper_utils as _paper_utils
from . import url_utils as _url_utils

# ---------------------------------------------------------------------------
# Tuning knobs (centralised)
# ---------------------------------------------------------------------------
MIN_SCORE_FLOOR: float = 0.30  # drop obviously low-score hits
ADAPTIVE_KEEP_FRAC: float = 0.25  # keep ≥25 % of pool (or ≥20)


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def filter_papers(
    papers: Iterable[Paper],
    k: int,
    *,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
) -> list[Paper]:
    """Backwards-compat convenience wrapper doing full pipeline."""

    pool = list(papers)
    pre = pre_filter_papers(pool, include_domains=include_domains, exclude_domains=exclude_domains)
    final = apply_score_threshold(pre, k)

    logger.debug(
        "filter_pipeline_stats: input={} pre={} final={} k_display={} keep_frac={}",
        len(pool),
        len(pre),
        len(final),
        k,
        ADAPTIVE_KEEP_FRAC,
    )
    return final


def pre_filter_papers(
    papers: Iterable[Paper],
    *,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
) -> list[Paper]:
    """Return a quality-filtered subset while ensuring breadth.

    Steps:
    1. validity + domain gating
    2. de-duplication by id / arXiv id / canonical URL
    """

    pool = list(papers)
    if not pool:
        return []

    # 1. validity & domain --------------------------------------------------
    stage1 = [
        p
        for p in pool
        if _paper_utils.is_valid_paper(p)
        and _url_utils.domain_allowed(p.url, include_domains, exclude_domains)
    ] or pool  # fallback if heuristics were too strict

    # 2. de-duplicate -------------------------------------------------------
    seen_keys: set[str] = set()
    deduped: list[Paper] = []

    def _canonical_url_key(u: str) -> str:
        """Return host+path normalised key for quick equivalence check."""
        pu = urlparse(u)
        host = pu.netloc.lower().removeprefix("www.")
        path = pu.path.rstrip("/")
        # Map arXiv /abs/1234.5678 and /pdf/ to common id for robustness.
        if "arxiv.org" in host or "ar5iv.org" in host:
            m = re.search(r"(?:abs|pdf)/(?P<id>\d{4}\.\d{4,5})", path)
            if m:
                return f"arxiv:{m.group('id')}"
        return f"{host}{path}"

    for p in stage1:
        url_str = str(p.url)
        candidate_keys: set[str] = {_canonical_url_key(url_str)}
        if arxiv_id := _url_utils.extract_arxiv_id(url_str):
            candidate_keys.add(f"arxiv:{arxiv_id}")
        if doi := _url_utils.extract_doi(url_str):
            candidate_keys.add(f"doi:{doi}")
        # Also include Paper.id if looks like a DOI or arXiv; helps when URL missing.
        if p.id:
            candidate_keys.add(p.id.lower())

        if candidate_keys & seen_keys:
            continue  # duplicate hit – skip

        seen_keys.update(candidate_keys)
        deduped.append(p)

    # Final O(N^2) check using robust URL equivalence matcher to catch alias cases
    unique: list[Paper] = []
    for p in deduped:
        if any(_url_utils.url_equivalent(str(p.url), str(q.url)) for q in unique):
            continue
        unique.append(p)
    deduped = unique

    if not deduped:
        return []

    return deduped


def apply_score_threshold(
    papers: Iterable[Paper],
    k: int,
) -> list[Paper]:
    """Apply adaptive score threshold to filtered papers."""

    ranked = sorted(
        papers,
        key=lambda x: x.score if x.score is not None else 1.0,
        reverse=True,
    )

    # Keep at least 25 % of the pool or 20 items irrespective of display
    # count so subsequent UI slices are stable.
    min_keep = max(int(len(ranked) * ADAPTIVE_KEEP_FRAC), 20)
    min_keep = min(min_keep, len(ranked))

    score_cut = ranked[min_keep - 1].score if ranked[min_keep - 1].score is not None else None
    threshold = max(MIN_SCORE_FLOOR, score_cut or 0.0)

    final_pool = [p for p in ranked if p.score is None or p.score >= threshold]

    if len(final_pool) < k:  # guarantee UI is not starved
        final_pool = ranked[: max(k * 3, len(ranked))]

    return final_pool
