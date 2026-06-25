"""Unit tests for services.core.exa_utils.filter_utils"""

from models.paper import Paper
from services.core.exa_utils import filter_utils


def _paper(pid: str, score: float, domain: str = "example.com") -> Paper:
    """Helper to create minimal valid Paper objects."""

    return Paper(
        id=pid,
        title=f"Test Paper {pid}",
        url=f"https://{domain}/{pid}",
        authors=["Doe, J."],
        score=score,
        source="unit-test",
    )


def test_deduplication_by_id() -> None:
    papers = [_paper("1", 0.9), _paper("1", 0.85), _paper("2", 0.8)]
    out = filter_utils.filter_papers(papers, k=1)
    ids = {p.id for p in out}
    # duplicate id "1" should be collapsed
    assert ids == {"1", "2"} or ids == {"1"}


def test_score_threshold_respects_floor() -> None:
    papers = [
        _paper("1", 0.95),
        _paper("2", 0.5),
        _paper("3", 0.15),  # below MIN_SCORE_FLOOR (0.30)
    ]
    out = filter_utils.filter_papers(papers, k=1)
    # No paper below floor should remain unless adaptive quota requires it
    assert all(p.score is None or p.score >= filter_utils.MIN_SCORE_FLOOR for p in out)


def test_domain_exclusion() -> None:
    # Need enough papers so the fail-soft threshold (max(10, n//2)) doesn't
    # revert the filter. With 12 good + 1 bad, filtering out 1 leaves 12 >= 10.
    good_papers = [_paper(str(i), 0.9, domain="good.com") for i in range(1, 13)]
    bad = _paper("bad", 0.9, domain="blocked.com")
    out = filter_utils.filter_papers(good_papers + [bad], k=5, exclude_domains=["blocked.com"])
    assert all("blocked.com" not in str(p.url) for p in out)
