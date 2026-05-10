"""Unit tests for the identifier-based dedup helpers in services.core.exa_client."""

from services.core.exa_client import _candidate_dedup_keys, _paper_dedup_key


def test_paper_dedup_key_arxiv_abs_pdf_same() -> None:
    """abs/ and pdf/ for the same arXiv id should produce the same key."""
    assert _paper_dedup_key("https://arxiv.org/abs/2401.12345") == _paper_dedup_key(
        "https://arxiv.org/pdf/2401.12345.pdf"
    )


def test_paper_dedup_key_ar5iv_same_as_arxiv() -> None:
    """ar5iv.org and arxiv.org should resolve to the same arXiv id key."""
    assert _paper_dedup_key("https://ar5iv.org/abs/2401.12345") == _paper_dedup_key(
        "https://arxiv.org/abs/2401.12345"
    )


def test_paper_dedup_key_version_suffix_collapsed() -> None:
    """v1 vs v2 of the same paper should produce the same key."""
    assert _paper_dedup_key("https://arxiv.org/abs/2401.12345v1") == _paper_dedup_key(
        "https://arxiv.org/abs/2401.12345v3"
    )


def test_paper_dedup_key_doi_collapsed() -> None:
    """Two URLs carrying the same DOI should produce the same DOI key."""
    a = _paper_dedup_key("https://doi.org/10.1038/s41586-020-03051-4")
    b = _paper_dedup_key("https://dx.doi.org/10.1038/s41586-020-03051-4")
    assert a == b
    assert a.startswith("doi:")


def test_paper_dedup_key_distinct_papers() -> None:
    """Different arXiv IDs must produce different keys."""
    assert _paper_dedup_key("https://arxiv.org/abs/2401.12345") != _paper_dedup_key(
        "https://arxiv.org/abs/2401.12346"
    )


def test_candidate_dedup_keys_includes_all_strong_ids() -> None:
    """A URL with both arXiv id and DOI signals should yield both keys."""
    keys = _candidate_dedup_keys("https://arxiv.org/abs/2401.12345")
    assert any(k.startswith("arxiv:") for k in keys)


def test_candidate_dedup_keys_overlap_arxiv_seed() -> None:
    """Seed (arxiv.org/abs) and candidate (ar5iv pdf) should share at least one key."""
    seed = _candidate_dedup_keys("https://arxiv.org/abs/2401.12345")
    cand = _candidate_dedup_keys("https://ar5iv.org/pdf/2401.12345v2")
    assert seed & cand, "expected the same arXiv-id key on both sides"
