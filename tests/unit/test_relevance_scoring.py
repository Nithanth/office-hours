"""Unit tests for compute_relevance_scores in services.core.exa_utils.filter_utils."""

from datetime import datetime

from models.paper import Paper
from services.core.exa_utils.filter_utils import (
    _TRUSTED_DOMAINS,
    compute_relevance_scores,
)


def _paper(
    pid: str,
    *,
    score: float | None = None,
    domain: str = "example.com",
    authors: list[str] | None = None,
    has_date: bool = True,
    text_len: int = 0,
) -> Paper:
    return Paper(
        id=pid,
        title=f"Paper {pid}",
        url=f"https://{domain}/{pid}",
        authors=authors if authors is not None else ["Doe, J."],
        score=score,
        source="unit-test",
        published_date=datetime(2024, 1, 1) if has_date else None,
        text_content="x" * text_len,
    )


def test_relevance_uses_exa_semantic_score() -> None:
    """When two papers have identical metadata but different Exa scores, the
    one with the higher Exa score should get the higher relevance_score."""
    high = _paper("hi", score=0.95)
    low = _paper("lo", score=0.10)
    compute_relevance_scores([high, low])
    assert high.relevance_score is not None and low.relevance_score is not None
    assert high.relevance_score > low.relevance_score


def test_relevance_falls_back_to_rank_when_score_missing() -> None:
    """When Exa scores are missing, falls back to rank position so the first
    paper still scores higher than the last."""
    a = _paper("a", score=None)
    b = _paper("b", score=None)
    compute_relevance_scores([a, b])
    assert a.relevance_score is not None and b.relevance_score is not None
    assert a.relevance_score >= b.relevance_score


def test_neurips_proceedings_domain_now_trusted() -> None:
    """NeurIPS proceedings should now be in _TRUSTED_DOMAINS — papers there
    should not get the untrusted-source penalty."""
    assert "papers.nips.cc" in _TRUSTED_DOMAINS
    assert "proceedings.neurips.cc" in _TRUSTED_DOMAINS
    assert "proceedings.mlr.press" in _TRUSTED_DOMAINS  # ICML
    assert "openaccess.thecvf.com" in _TRUSTED_DOMAINS  # CVPR/ICCV
    assert "ojs.aaai.org" in _TRUSTED_DOMAINS  # AAAI


def test_relevance_trusted_domain_outscores_untrusted_at_same_exa_score() -> None:
    """Two papers with identical Exa scores: the one on a trusted academic
    domain should outrank the one on a generic domain."""
    trusted = _paper("t", score=0.7, domain="papers.nips.cc")
    untrusted = _paper("u", score=0.7, domain="randomblog.example.com")
    compute_relevance_scores([trusted, untrusted])
    assert trusted.relevance_score is not None and untrusted.relevance_score is not None
    assert trusted.relevance_score > untrusted.relevance_score
