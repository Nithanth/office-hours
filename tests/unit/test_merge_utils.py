"""Unit tests for services.core.exa_utils.merge_utils."""

from services.core.exa_utils import merge_utils as mu


def test_merge_dicts_keep_first() -> None:
    high = {"title": "Ground-Truth", "authors": ["A"], "overwrite_me": "HIGH"}
    low = {"title": "Noisy", "published_date": "2020-01-01", "overwrite_me": "low"}
    merged = mu.merge_dicts_keep_first(high, low)
    assert merged == {
        "title": "Noisy",  # low-trust value retained because key already present
        "authors": ["A"],
        "published_date": "2020-01-01",
        "overwrite_me": "low",  # existing value kept
    }


def test_sha256() -> None:
    assert mu.sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
