"""Unit tests covering TextExtractor helper methods (cache path, cleaning, quality)."""

from __future__ import annotations

from services.paper.text_extractor import (
    TextExtractor,
    _clean_text,
)  # noqa: D100 – tests


def test_clean_text_collapses_spaces():
    raw = """\
    Line one


    Line two
    """
    cleaned = _clean_text(raw)
    # No trailing spaces and single blank line
    assert "  " not in cleaned
    assert "\n\n\n" not in cleaned


def test_good_quality_threshold():
    short: str = "a" * 2000
    long_txt: str = "b" * 4000
    assert not TextExtractor._good_quality(short)
    assert TextExtractor._good_quality(long_txt)


def test_cache_path_stable():
    url: str = "https://example.org/paper.pdf"
    pid: str = "abc123"
    extract = TextExtractor.instance()
    p1 = extract._cache_path(url, pid)
    p2 = extract._cache_path(url, pid)
    assert p1 == p2  # deterministic
    assert pid in p1.name
