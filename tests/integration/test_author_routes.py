"""Integration tests for author search routes.

Uses the shared ``client`` fixture from conftest.py which stubs out
all external services (LLM, Exa, etc.) via monkeypatch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from services.paper.metadata.author_search import (
    AuthorPaper,
    AuthorProfile,
    AuthorResult,
)

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_SAMPLE_AUTHORS = [
    AuthorResult(
        author_id="1234567",
        name="Yann LeCun",
        aliases=["Y. LeCun"],
        paper_count=450,
        citation_count=200000,
        h_index=120,
    ),
]

_SAMPLE_PROFILE = AuthorProfile(
    author=_SAMPLE_AUTHORS[0],
    papers=[
        AuthorPaper(
            paper_id="abc123",
            title="Deep Learning",
            year=2015,
            citation_count=30000,
            venue="Nature",
            url="https://www.semanticscholar.org/paper/abc123",
            abstract="Deep learning allows computational models...",
            external_ids={"ArXiv": "1521.00561"},
        ),
        AuthorPaper(
            paper_id="def456",
            title="Gradient-based learning",
            year=1998,
            citation_count=25000,
            venue="Proceedings of the IEEE",
            url="https://www.semanticscholar.org/paper/def456",
            external_ids={"DOI": "10.1109/5.726791"},
        ),
    ],
)


# ---------------------------------------------------------------------------
# POST /author/search
# ---------------------------------------------------------------------------


class TestAuthorSearchRoute:
    """Tests for POST /author/search."""

    @patch("api.routes.author.search_authors")
    def test_search_returns_html(self, mock_search, client: TestClient):
        mock_search.return_value = _SAMPLE_AUTHORS
        resp = client.post("/author/search", data={"search_query": "Yann LeCun"})
        assert resp.status_code == 200
        assert "Yann LeCun" in resp.text
        assert "200,000" in resp.text  # formatted citation count
        assert "h-index 120" in resp.text

    @patch("api.routes.author.search_authors")
    def test_empty_query_rejected(self, mock_search, client: TestClient):
        resp = client.post("/author/search", data={"search_query": ""})
        assert resp.status_code == 200
        assert "enter an author name" in resp.text.lower()
        mock_search.assert_not_called()

    @patch("api.routes.author.search_authors")
    def test_no_results(self, mock_search, client: TestClient):
        mock_search.return_value = []
        resp = client.post("/author/search", data={"search_query": "Nonexistent"})
        assert resp.status_code == 200
        assert "No authors found" in resp.text

    @patch("api.routes.author.search_authors")
    def test_api_failure_handled(self, mock_search, client: TestClient):
        mock_search.side_effect = Exception("S2 API down")
        resp = client.post("/author/search", data={"search_query": "Test"})
        assert resp.status_code == 200
        assert "failed" in resp.text.lower() or "error" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /author/profile
# ---------------------------------------------------------------------------


class TestAuthorProfileRoute:
    """Tests for POST /author/profile."""

    @patch("api.routes.author.get_author_profile")
    def test_profile_returns_papers(self, mock_profile, client: TestClient):
        mock_profile.return_value = _SAMPLE_PROFILE
        resp = client.post(
            "/author/profile",
            data={"author_id": "1234567", "author_name": "Yann LeCun"},
        )
        assert resp.status_code == 200
        assert "Yann LeCun" in resp.text
        assert "Deep Learning" in resp.text
        assert "Generate Research Profile" in resp.text

    @patch("api.routes.author.get_author_profile")
    def test_profile_not_found(self, mock_profile, client: TestClient):
        mock_profile.return_value = None
        resp = client.post(
            "/author/profile",
            data={"author_id": "nonexistent", "author_name": "Nobody"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    @patch("api.routes.author.get_author_profile")
    def test_profile_api_error(self, mock_profile, client: TestClient):
        mock_profile.side_effect = Exception("Network error")
        resp = client.post(
            "/author/profile",
            data={"author_id": "123", "author_name": "Test"},
        )
        assert resp.status_code == 200
        assert "error" in resp.text.lower()

    @patch("api.routes.author.get_author_profile")
    def test_profile_has_author_stats(self, mock_profile, client: TestClient):
        mock_profile.return_value = _SAMPLE_PROFILE
        resp = client.post(
            "/author/profile",
            data={"author_id": "1234567", "author_name": "Yann LeCun"},
        )
        assert "450" in resp.text  # paper count
        assert "h-index 120" in resp.text

    @patch("api.routes.author.get_author_profile")
    def test_papers_converted_with_arxiv_urls(self, mock_profile, client: TestClient):
        """Papers with arXiv IDs should get arxiv.org URLs."""
        mock_profile.return_value = _SAMPLE_PROFILE
        resp = client.post(
            "/author/profile",
            data={"author_id": "1234567", "author_name": "Test"},
        )
        # The first paper has ArXiv ID 1521.00561
        assert "arxiv.org" in resp.text


# ---------------------------------------------------------------------------
# POST /author/research-profile
# ---------------------------------------------------------------------------


class TestResearchProfileRoute:
    """Tests for POST /author/research-profile."""

    @patch("api.routes.author.get_author_profile")
    def test_profile_generation(self, mock_profile, client: TestClient):
        mock_profile.return_value = _SAMPLE_PROFILE

        import main

        main.llm_service_instance.generate_completion = AsyncMock(
            return_value="## Research Focus\n\nYann LeCun is a pioneer in deep learning..."
        )

        resp = client.post(
            "/author/research-profile",
            data={"author_id": "1234567", "author_name": "Yann LeCun"},
        )
        assert resp.status_code == 200
        assert "Research Profile" in resp.text
        assert "Yann LeCun" in resp.text

    @patch("api.routes.author.get_author_profile")
    def test_profile_author_not_found(self, mock_profile, client: TestClient):
        mock_profile.return_value = None
        resp = client.post(
            "/author/research-profile",
            data={"author_id": "bad", "author_name": "Nobody"},
        )
        assert resp.status_code == 200
        assert "not found" in resp.text.lower()

    @patch("api.routes.author.get_author_profile")
    def test_llm_generation_error(self, mock_profile, client: TestClient):
        mock_profile.return_value = _SAMPLE_PROFILE

        import main

        main.llm_service_instance.generate_completion = AsyncMock(
            side_effect=Exception("LLM timeout")
        )

        resp = client.post(
            "/author/research-profile",
            data={"author_id": "1234567", "author_name": "Test"},
        )
        assert resp.status_code == 200
        assert "error" in resp.text.lower()
