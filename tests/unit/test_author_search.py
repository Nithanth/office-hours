"""Unit tests for Semantic Scholar author search service."""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from services.paper.metadata.author_search import (
    AuthorPaper,
    AuthorProfile,
    AuthorResult,
    S2UnavailableError,
    get_author_papers,
    get_author_profile,
    get_paper_url,
    search_authors,
)


def _http_error(code: int) -> HTTPError:
    return HTTPError("http://x", code, "err", {}, None)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_SAMPLE_AUTHOR_SEARCH_RESPONSE = {
    "total": 2,
    "data": [
        {
            "authorId": "1234567",
            "name": "Yann LeCun",
            "aliases": ["Y. LeCun", "Y LeCun"],
            "url": "https://www.semanticscholar.org/author/1234567",
            "paperCount": 450,
            "citationCount": 200000,
            "hIndex": 120,
        },
        {
            "authorId": "9876543",
            "name": "Yann LeCun Jr",
            "aliases": [],
            "url": None,
            "paperCount": 5,
            "citationCount": 10,
            "hIndex": 2,
        },
    ],
}

_SAMPLE_PAPERS_RESPONSE = {
    "data": [
        {
            "paperId": "abc123",
            "title": "Deep Learning",
            "year": 2015,
            "citationCount": 30000,
            "venue": "Nature",
            "url": "https://www.semanticscholar.org/paper/abc123",
            "abstract": "Deep learning allows computational models...",
            "externalIds": {"ArXiv": "1521.00561", "DOI": "10.1038/nature14539"},
        },
        {
            "paperId": "def456",
            "title": "Gradient-based learning applied to document recognition",
            "year": 1998,
            "citationCount": 25000,
            "venue": "Proceedings of the IEEE",
            "url": "https://www.semanticscholar.org/paper/def456",
            "abstract": None,
            "externalIds": {"DOI": "10.1109/5.726791"},
        },
        {
            "paperId": "ghi789",
            "title": "A paper with no external ids",
            "year": 2023,
            "citationCount": 5,
            "venue": None,
            "url": None,
            "abstract": "A test abstract.",
            "externalIds": {},
        },
    ],
}

_SAMPLE_AUTHOR_DETAILS = {
    "authorId": "1234567",
    "name": "Yann LeCun",
    "aliases": ["Y. LeCun"],
    "url": "https://www.semanticscholar.org/author/1234567",
    "paperCount": 450,
    "citationCount": 200000,
    "hIndex": 120,
}


def _mock_s2_get(path: str, params=None, *, timeout=10):
    """Route mock responses based on S2 API path."""
    if "/author/search" in path:
        return _SAMPLE_AUTHOR_SEARCH_RESPONSE
    elif "/papers" in path:
        return _SAMPLE_PAPERS_RESPONSE
    elif "/author/" in path:
        return _SAMPLE_AUTHOR_DETAILS
    raise ValueError(f"Unmocked S2 path: {path}")


# ---------------------------------------------------------------------------
# AuthorResult model tests
# ---------------------------------------------------------------------------


class TestAuthorResult:
    """Tests for the AuthorResult Pydantic model."""

    def test_basic_construction(self):
        a = AuthorResult(
            author_id="123",
            name="Alice Researcher",
            aliases=["A. Researcher"],
            paper_count=50,
            citation_count=5000,
            h_index=30,
        )
        assert a.author_id == "123"
        assert a.name == "Alice Researcher"
        assert a.aliases == ["A. Researcher"]
        assert a.paper_count == 50
        assert a.citation_count == 5000
        assert a.h_index == 30

    def test_defaults(self):
        a = AuthorResult(author_id="1", name="Test")
        assert a.aliases == []
        assert a.url is None
        assert a.paper_count == 0
        assert a.citation_count == 0
        assert a.h_index == 0


# ---------------------------------------------------------------------------
# AuthorPaper model tests
# ---------------------------------------------------------------------------


class TestAuthorPaper:
    """Tests for the AuthorPaper Pydantic model."""

    def test_basic_construction(self):
        p = AuthorPaper(
            paper_id="abc",
            title="Test Paper",
            year=2023,
            citation_count=100,
            venue="NeurIPS",
            abstract="An abstract.",
            external_ids={"ArXiv": "2301.00001"},
        )
        assert p.paper_id == "abc"
        assert p.title == "Test Paper"
        assert p.year == 2023
        assert p.venue == "NeurIPS"
        assert p.external_ids["ArXiv"] == "2301.00001"

    def test_defaults(self):
        p = AuthorPaper(paper_id="x", title="Minimal")
        assert p.year is None
        assert p.citation_count == 0
        assert p.venue is None
        assert p.abstract is None
        assert p.external_ids == {}


# ---------------------------------------------------------------------------
# search_authors tests
# ---------------------------------------------------------------------------


class TestSearchAuthors:
    """Tests for search_authors function."""

    @patch("services.paper.metadata.author_search._s2_get")
    def test_basic_search(self, mock_get):
        mock_get.return_value = _SAMPLE_AUTHOR_SEARCH_RESPONSE
        results = search_authors("Yann LeCun")

        assert len(results) == 2
        assert results[0].author_id == "1234567"
        assert results[0].name == "Yann LeCun"
        assert results[0].aliases == ["Y. LeCun", "Y LeCun"]
        assert results[0].paper_count == 450
        assert results[0].citation_count == 200000
        assert results[0].h_index == 120

        # Check the API was called correctly
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[0][0] == "/author/search"
        assert "Yann LeCun" in call_args[1]["params"]["query"]

    @patch("services.paper.metadata.author_search._s2_get")
    def test_empty_query(self, mock_get):
        results = search_authors("")
        assert results == []
        mock_get.assert_not_called()

    @patch("services.paper.metadata.author_search._s2_get")
    def test_whitespace_query(self, mock_get):
        results = search_authors("   ")
        assert results == []
        mock_get.assert_not_called()

    @patch("services.paper.metadata.author_search._s2_get")
    def test_api_error_raises_unavailable(self, mock_get):
        """Transport failures must surface, not masquerade as zero results."""
        mock_get.side_effect = Exception("Network error")
        with pytest.raises(S2UnavailableError):
            search_authors("Test Author")

    @patch("services.paper.metadata.author_search._s2_get")
    def test_rate_limit_raises_unavailable(self, mock_get):
        mock_get.side_effect = _http_error(429)
        with pytest.raises(S2UnavailableError):
            search_authors("Test Author")

    @patch("services.paper.metadata.author_search._s2_get")
    def test_empty_response(self, mock_get):
        mock_get.return_value = {"total": 0, "data": []}
        results = search_authors("Nonexistent Author")
        assert results == []

    @patch("services.paper.metadata.author_search._s2_get")
    def test_limit_parameter(self, mock_get):
        mock_get.return_value = {"total": 0, "data": []}
        search_authors("Test", limit=3)
        call_params = mock_get.call_args[1]["params"]
        assert call_params["limit"] == "3"

    @patch("services.paper.metadata.author_search._s2_get")
    def test_limit_clamped_to_1000(self, mock_get):
        mock_get.return_value = {"total": 0, "data": []}
        search_authors("Test", limit=5000)
        call_params = mock_get.call_args[1]["params"]
        assert call_params["limit"] == "1000"

    @patch("services.paper.metadata.author_search._s2_get")
    def test_null_fields_handled(self, mock_get):
        """None values in S2 response should default gracefully."""
        mock_get.return_value = {
            "total": 1,
            "data": [
                {
                    "authorId": "111",
                    "name": "Test",
                    "aliases": None,
                    "url": None,
                    "paperCount": None,
                    "citationCount": None,
                    "hIndex": None,
                }
            ],
        }
        results = search_authors("Test")
        assert len(results) == 1
        assert results[0].aliases == []
        assert results[0].paper_count == 0
        assert results[0].citation_count == 0
        assert results[0].h_index == 0


# ---------------------------------------------------------------------------
# get_author_papers tests
# ---------------------------------------------------------------------------


class TestGetAuthorPapers:
    """Tests for get_author_papers function."""

    @patch("services.paper.metadata.author_search._s2_get")
    def test_basic_fetch(self, mock_get):
        mock_get.return_value = _SAMPLE_PAPERS_RESPONSE
        papers = get_author_papers("1234567")

        assert len(papers) == 3
        # Should be sorted by citation count descending
        assert papers[0].citation_count >= papers[1].citation_count
        assert papers[0].title == "Deep Learning"
        assert papers[0].year == 2015

    @patch("services.paper.metadata.author_search._s2_get")
    def test_api_error_raises_unavailable(self, mock_get):
        mock_get.side_effect = Exception("Timeout")
        with pytest.raises(S2UnavailableError):
            get_author_papers("1234567")

    @patch("services.paper.metadata.author_search._s2_get")
    def test_papers_sorted_by_citations(self, mock_get):
        mock_get.return_value = {
            "data": [
                {"paperId": "a", "title": "Low", "citationCount": 5, "externalIds": {}},
                {
                    "paperId": "b",
                    "title": "High",
                    "citationCount": 500,
                    "externalIds": {},
                },
                {
                    "paperId": "c",
                    "title": "Mid",
                    "citationCount": 50,
                    "externalIds": {},
                },
            ]
        }
        papers = get_author_papers("123")
        assert papers[0].title == "High"
        assert papers[1].title == "Mid"
        assert papers[2].title == "Low"

    @patch("services.paper.metadata.author_search._s2_get")
    def test_null_fields_in_papers(self, mock_get):
        mock_get.return_value = {
            "data": [
                {
                    "paperId": "x",
                    "title": "Minimal Paper",
                    "year": None,
                    "citationCount": None,
                    "venue": None,
                    "url": None,
                    "abstract": None,
                    "externalIds": None,
                }
            ]
        }
        papers = get_author_papers("123")
        assert len(papers) == 1
        assert papers[0].year is None
        assert papers[0].citation_count == 0
        assert papers[0].venue is None
        assert papers[0].external_ids == {}


# ---------------------------------------------------------------------------
# get_author_profile tests
# ---------------------------------------------------------------------------


class TestGetAuthorProfile:
    """Tests for get_author_profile function."""

    @patch("services.paper.metadata.author_search._s2_get")
    def test_full_profile(self, mock_get):
        mock_get.side_effect = _mock_s2_get
        profile = get_author_profile("1234567", paper_limit=10)

        assert profile is not None
        assert profile.author.name == "Yann LeCun"
        assert profile.author.h_index == 120
        assert len(profile.papers) == 3

    @patch("services.paper.metadata.author_search._s2_get")
    def test_author_not_found(self, mock_get):
        mock_get.side_effect = _http_error(404)
        profile = get_author_profile("nonexistent")
        assert profile is None

    @patch("services.paper.metadata.author_search._s2_get")
    def test_profile_error_raises_unavailable(self, mock_get):
        mock_get.side_effect = _http_error(429)
        with pytest.raises(S2UnavailableError):
            get_author_profile("1234567")

    @patch("services.paper.metadata.author_search._s2_get")
    def test_profile_type(self, mock_get):
        mock_get.side_effect = _mock_s2_get
        profile = get_author_profile("1234567")
        assert isinstance(profile, AuthorProfile)
        assert isinstance(profile.author, AuthorResult)
        assert all(isinstance(p, AuthorPaper) for p in profile.papers)


# ---------------------------------------------------------------------------
# get_paper_url tests
# ---------------------------------------------------------------------------


class TestGetPaperUrl:
    """Tests for get_paper_url URL derivation logic."""

    def test_arxiv_preferred(self):
        """arXiv URLs take priority over DOI and S2 URL."""
        p = AuthorPaper(
            paper_id="abc",
            title="Test",
            url="https://semanticscholar.org/paper/abc",
            external_ids={"ArXiv": "2301.00001", "DOI": "10.1234/test"},
        )
        url = get_paper_url(p)
        assert url == "https://arxiv.org/abs/2301.00001"

    def test_doi_fallback(self):
        """DOI used when no arXiv ID."""
        p = AuthorPaper(
            paper_id="abc",
            title="Test",
            external_ids={"DOI": "10.1038/nature14539"},
        )
        url = get_paper_url(p)
        assert url == "https://doi.org/10.1038/nature14539"

    def test_s2_url_fallback(self):
        """Paper's own URL used when no external IDs."""
        p = AuthorPaper(
            paper_id="abc",
            title="Test",
            url="https://www.semanticscholar.org/paper/abc",
            external_ids={},
        )
        url = get_paper_url(p)
        assert url == "https://www.semanticscholar.org/paper/abc"

    def test_paper_id_last_resort(self):
        """S2 paper page used as last resort."""
        p = AuthorPaper(
            paper_id="abc123def",
            title="Test",
            url=None,
            external_ids={},
        )
        url = get_paper_url(p)
        assert url == "https://www.semanticscholar.org/paper/abc123def"

    def test_no_url_possible(self):
        """Returns None when no URL can be derived."""
        p = AuthorPaper(
            paper_id="",
            title="Test",
            url=None,
            external_ids={},
        )
        url = get_paper_url(p)
        assert url is None


# ---------------------------------------------------------------------------
# Route rendering helpers (unit tests for HTML output)
# ---------------------------------------------------------------------------


class TestRenderAuthorCard:
    """Tests for _render_author_card HTML generation."""

    def test_basic_rendering(self):
        from api.routes.author import _render_author_card

        author = AuthorResult(
            author_id="123",
            name="Alice Bob",
            aliases=["A. Bob"],
            paper_count=100,
            citation_count=5000,
            h_index=25,
        )
        html = _render_author_card(author)
        assert "Alice Bob" in html
        assert "100" in html  # paper count
        assert "5,000" in html  # citation count formatted
        assert "h-index 25" in html
        assert "A. Bob" in html  # alias
        assert "hx-post" in html  # HTMX binding
        assert "/author/profile" in html

    def test_xss_prevention(self):
        """Author names with HTML should be escaped."""
        from api.routes.author import _render_author_card

        author = AuthorResult(
            author_id="1",
            name="<script>alert('xss')</script>",
        )
        html = _render_author_card(author)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_no_aliases(self):
        from api.routes.author import _render_author_card

        author = AuthorResult(author_id="1", name="Test")
        html = _render_author_card(author)
        assert "Also:" not in html


class TestRenderAuthorList:
    """Tests for _render_author_list HTML generation."""

    def test_empty_results(self):
        from api.routes.author import _render_author_list

        html = _render_author_list([], "Test Query")
        assert "No authors found" in html
        assert "Test Query" in html

    def test_with_results(self):
        from api.routes.author import _render_author_list

        authors = [
            AuthorResult(author_id="1", name="Author One", paper_count=10),
            AuthorResult(author_id="2", name="Author Two", paper_count=20),
        ]
        html = _render_author_list(authors, "test")
        assert "Author One" in html
        assert "Author Two" in html
        assert "Found 2 author(s)" in html
