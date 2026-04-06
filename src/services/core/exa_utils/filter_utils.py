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


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

# Authoritative academic domains earn a source-quality bonus.
_TRUSTED_DOMAINS: set[str] = {
    "arxiv.org", "ar5iv.org", "biorxiv.org", "medrxiv.org",
    "ieee.org", "ieeexplore.ieee.org", "dl.acm.org",
    "openreview.net", "aclanthology.org", "aclweb.org",
    "nature.com", "science.org", "pnas.org", "springer.com",
    "wiley.com", "cell.com", "nih.gov", "ncbi.nlm.nih.gov",
}


def compute_relevance_scores(papers: list[Paper]) -> None:
    """Assign ``paper.relevance_score`` (0.00–1.00) to every paper **in-place**.

    Composite score blending Exa's rank-order relevance with paper quality
    signals.  Each component is normalised to 0–1, then weighted:

        rank_signal      (0.40) — Exa's result ordering is their relevance
        source_authority (0.25) — trusted academic domains score higher
        metadata_quality (0.20) — authors, date, completeness
        content_richness (0.15) — how much extractable text we have
    """
    if not papers:
        return

    n = len(papers)

    for rank, p in enumerate(papers):
        # --- Rank signal (0–1, top=1.0, bottom=0.0) -------------------------
        rank_signal = 1.0 - (rank / max(n - 1, 1))

        # --- Source authority (0–1) -----------------------------------------
        url_str = str(p.url).lower() if p.url else ""
        host = urlparse(url_str).netloc.removeprefix("www.")
        is_trusted = any(host == d or host.endswith("." + d) for d in _TRUSTED_DOMAINS)
        source_authority = 1.0 if is_trusted else 0.3

        # --- Metadata quality (0–1) -----------------------------------------
        meta_score = 0.0
        has_authors = bool(p.authors and len(p.authors) > 0)
        has_date = bool(p.published_date)
        has_title = bool(p.title and p.title not in ("No Title Provided", ""))
        # Each component worth ~0.33
        if has_authors:
            meta_score += 0.40
        if has_date:
            meta_score += 0.30
        if has_title:
            meta_score += 0.30

        # --- Content richness (0–1) -----------------------------------------
        text_len = len(p.text_content or "")
        if text_len > 5000:
            content_score = 1.0
        elif text_len > 2000:
            content_score = 0.8
        elif text_len > 500:
            content_score = 0.5
        elif text_len > 50:
            content_score = 0.2
        else:
            content_score = 0.0

        # --- Weighted composite ---------------------------------------------
        final = (
            0.40 * rank_signal
            + 0.25 * source_authority
            + 0.20 * meta_score
            + 0.15 * content_score
        )
        p.relevance_score = round(max(0.0, min(1.0, final)), 2)


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

    logger.debug(
        "stage1_filter_stats", total=len(pool), kept=len(stage1), dropped=len(pool) - len(stage1)
    )
    # Fail-soft: if we kept too few (heuristics overshot), revert to pool.
    # Triggers when <50% or <10 items remain, whichever is larger.
    min_keep_soft = max(10, len(pool) // 2)
    if len(stage1) < min_keep_soft:
        logger.debug(
            "stage1_fail_soft_triggered", total=len(pool), kept=len(stage1), min_keep_soft=min_keep_soft
        )
        stage1 = pool

    logger.debug("STAGE1_POOL ({})", len(stage1))
    for i, _p in enumerate(stage1, 1):
        _score = getattr(_p, "score", None)
        _s = f"score={_score:.3f}" if _score is not None else "score=?"
        _title = (_p.title or "").strip()
        _url = str(_p.url) if _p.url else ""
        logger.debug("STAGE1 {:02d} {} | {} | {}", i, _s, _title, _url)

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
        candidate_keys: set[str] = set()
        arxiv_id = _url_utils.extract_arxiv_id(url_str)
        doi = _url_utils.extract_doi(url_str)

        # Prefer strong identifiers over canonical host+path. For arXiv/DOI we
        # only use the strong key so variants (pdf/abs/ar5iv) are not collapsed
        # beyond the ID itself.
        if arxiv_id:
            candidate_keys.add(f"arxiv:{arxiv_id}")
        elif doi:
            candidate_keys.add(f"doi:{doi}")
        else:
            candidate_keys.add(_canonical_url_key(url_str))

        # Include Paper.id only when it resembles a trusted ID to avoid
        # over-collapsing on opaque ids.
        if p.id:
            pid = p.id.lower()
            if _url_utils.extract_arxiv_id(pid):
                candidate_keys.add(f"arxiv:{_url_utils.extract_arxiv_id(pid)}")
            elif _url_utils.extract_doi(pid):
                candidate_keys.add(f"doi:{_url_utils.extract_doi(pid)}")

        if candidate_keys & seen_keys:
            logger.debug("dup_skip", title=p.title, keys=list(candidate_keys))
            continue  # duplicate hit – skip

        seen_keys.update(candidate_keys)
        deduped.append(p)

    # Final O(N^2) check using robust URL equivalence matcher to catch alias cases
    unique: list[Paper] = []
    for p in deduped:
        # Do not collapse arXiv variants here; we already deduped by arXiv ID.
        if not _url_utils.extract_arxiv_id(str(p.url)):
            if any(_url_utils.url_equivalent(str(p.url), str(q.url)) for q in unique):
                continue
        unique.append(p)
    deduped = unique

    logger.debug(
        "dedup_filter_stats", after_stage1=len(stage1), kept=len(deduped), dropped=len(stage1)-len(deduped)
    )
    logger.debug("DEDUP_POOL ({})", len(deduped))
    for i, _p in enumerate(deduped, 1):
        _score = getattr(_p, "score", None)
        _s = f"score={_score:.3f}" if _score is not None else "score=?"
        _title = (_p.title or "").strip()
        _url = str(_p.url) if _p.url else ""
        logger.debug("DEDUP {:02d} {} | {} | {}", i, _s, _title, _url)

    if not deduped:
        return []

    return deduped


def apply_score_threshold(
    papers: Iterable[Paper],
    k: int,
) -> list[Paper]:
    """Apply adaptive score threshold to filtered papers.

    Exa 2.x returns ``score=None`` for all results. When scores are absent
    we preserve the original Exa rank order (which *is* their relevance
    ranking) and only trim to a sensible pool size.
    """

    pool = list(papers)

    # Check if we actually have scores to threshold on
    has_scores = any(p.score is not None for p in pool)

    if not has_scores:
        # No scores — keep all papers in Exa's rank order, trimmed to a
        # reasonable pool size so the UI isn't overwhelmed.
        min_keep = max(int(len(pool) * ADAPTIVE_KEEP_FRAC), 20)
        final_pool = pool[:max(min_keep, k * 3, len(pool))]
        logger.debug(
            "apply_score_threshold_stats (no scores)", pool=len(pool), kept=len(final_pool)
        )
        return final_pool

    ranked = sorted(
        pool,
        key=lambda x: x.score if x.score is not None else 1.0,
        reverse=True,
    )

    min_keep = max(int(len(ranked) * ADAPTIVE_KEEP_FRAC), 20)
    min_keep = min(min_keep, len(ranked))

    score_cut = ranked[min_keep - 1].score if ranked[min_keep - 1].score is not None else None

    if len(ranked) < 20:
        threshold = MIN_SCORE_FLOOR
        logger.debug(
            "small_pool_threshold", size=len(ranked), threshold=threshold, score_cut=score_cut
        )
    else:
        threshold = max(MIN_SCORE_FLOOR, score_cut or 0.0)

    final_pool = [p for p in ranked if p.score is None or p.score >= threshold]

    logger.debug(
        "apply_score_threshold_stats", pool=len(ranked), kept=len(final_pool), threshold=threshold
    )

    if len(final_pool) < k:
        final_pool = ranked[: max(k * 3, len(ranked))]

    return final_pool
