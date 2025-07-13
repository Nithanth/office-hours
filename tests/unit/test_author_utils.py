"""Unit tests for infra.exa_utils.author_utils."""

from infra.exa_utils import author_utils as au


def test_is_plausible_author_valid() -> None:
    assert au.is_plausible_author("John Smith") is True
    assert au.is_plausible_author("A. B. C. Johnson") is True


def test_is_plausible_author_invalid() -> None:
    for bad in ["konasj", "[arXiv]", "submitted 2020", "1234", "verylongusername"]:
        assert au.is_plausible_author(bad) is False


def test_filter_plausible_authors() -> None:
    # Single-token capitalised name like "Alice" is considered implausible by the
    # current heuristic (matches original ExaClient behaviour).
    names = ["John Smith", "konasj", "Alice"]
    assert au.filter_plausible_authors(names) == ["John Smith"]
