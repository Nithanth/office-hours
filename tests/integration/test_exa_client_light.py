# tests/integration/test_exa_client_light.py

"""Light-weight behavioural smoke tests for `services.core.exa_client.ExaClient`.

These tests purposely avoid **any** network traffic or real *exa_py* dependency
by monkey-patching the `ExaAdapter` used by `ExaClient`.  The goal is to catch
accidental behaviour drift in two critical areas:

1. `_update_paper_from_meta` - author-replacement heuristic.
2. `find_similar_papers` - happy-path flow and result count trimming.

The dummy adapter returns deterministic fake results so CI can run the suite
without an `EXA_API_KEY` or external services.
"""

from __future__ import annotations

from typing import Any

import pytest

from models.paper import Paper  # noqa: E402

# Import inside tests so the monkey-patch in the autouse fixture affects the
# module when it is first loaded.
from services.core.exa_client import ExaClient  # noqa: E402
from services.core.exa_utils import paper_utils as _paper_utils  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures - stub ExaAdapter & heavy helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_exa_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `ExaAdapter` with a minimal in-memory fake and skip enrichment."""

    class _FakeResult:  # pylint: disable=too-few-public-methods
        """Mimic a bare-minimum Exa SDK result object."""

        def __init__(self, idx: int):
            self.id: str = f"doc{idx}"
            # arXiv domain keeps domain filters happy
            self.url: str = f"https://arxiv.org/pdf/21{idx:02d}.0000{idx}.pdf"
            # Ensure title length >= 8 to pass `_is_valid_paper` filter
            self.title: str = f"Example Paper {idx}"
            # Good author tokens (contain spaces)
            self.authors: list[str] = ["Alice Smith", "Bob Jones"]
            self.score: float = 1.0 - idx * 0.01  # slight variation
            self.text: str | None = None
            self.summary: str | None = None
            self.published_date: str | None = None

    class _FakeResponse:  # pylint: disable=too-few-public-methods
        """Container mirroring the SDK response shape (just `.results`)."""

        def __init__(self, results: list[_FakeResult]):
            self.results = results

    class _FakeAdapter:  # pylint: disable=too-few-public-methods
        """Stand-in for `infra.exa_adapter.ExaAdapter`."""

        def __init__(self, api_key: str):  # noqa: D401 - signature match
            # No external validation
            pass

        @property
        def sync_client(self) -> _FakeAdapter:  # noqa: D401 - property helper
            return self

        # ------------------------------------------------------------------
        # Methods used by ExaClient during these tests
        # ------------------------------------------------------------------
        def find_similar_and_contents(self, url: str, **kwargs: Any) -> _FakeResponse:  # noqa: D401
            # Always return 5 deterministic fake results irrespective of params.
            results = [_FakeResult(i) for i in range(5)]
            return _FakeResponse(results)

    # Install stubs
    monkeypatch.setattr("services.core.exa_client.ExaAdapter", _FakeAdapter)
    # Skip network-heavy enrichment entirely - just echo the Paper back.
    monkeypatch.setattr(
        "services.core.exa_client.ExaClient._enrich_paper_metadata", lambda self, p: p
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_update_paper_from_meta_author_heuristic() -> None:
    """Valid author list should *not* be overwritten by poorer metadata, and vice-versa."""

    # Case-1: existing good authors should remain when meta has single-token names
    good_paper = Paper(
        id="p1",
        title="Title A",
        url="https://example.com/p1",
        authors=["Yee Whye Teh", "Alice Smith"],
    )
    meta_poor = {"authors": ["Teh", "Smith"]}
    updated = _paper_utils.update_paper_from_meta(good_paper, meta_poor)
    assert updated.authors == good_paper.authors  # unchanged

    # Case-2: poor existing list should be replaced by richer meta
    poor_paper = Paper(
        id="p2",
        title="Title B",
        url="https://example.com/p2",
        authors=["Dupont"],  # single-token ⇒ poor quality
    )
    meta_good = {
        "authors": ["Alice Smith", "Bob Jones", "Charlie Doe"],
    }
    updated2 = _paper_utils.update_paper_from_meta(poor_paper, meta_good)
    assert updated2.authors == meta_good["authors"]


def test_find_similar_papers_basic_flow() -> None:
    """`find_similar_papers` should return the requested count with plausible data."""

    # Request 3 similar papers; our fake adapter always yields 5 raw results -
    # the client should trim to the desired count.
    papers = ExaClient(api_key="fake_key").find_similar_papers(
        paper_url="https://arxiv.org/pdf/2103.03123.pdf",
        num_results=3,
    )

    assert len(papers) == 3
    # Basic sanity of returned `Paper` objects
    for p in papers:
        assert p.title.startswith("Example Paper ")
        assert p.authors, "Authors should not be empty"
        assert str(p.url).startswith("https://arxiv.org/")
