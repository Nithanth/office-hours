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

    @patch("services.paper.metadata.author_search._oa_get")
    @patch("services.paper.metadata.author_search._s2_get")
    def test_api_error_raises_unavailable(self, mock_get, mock_oa):
        """Transport failures surface only when the fallback also fails."""
        mock_get.side_effect = Exception("Network error")
        mock_oa.side_effect = Exception("openalex down")
        with pytest.raises(S2UnavailableError):
            search_authors("Test Author")

    @patch("services.paper.metadata.author_search._oa_get")
    @patch("services.paper.metadata.author_search._s2_get")
    def test_rate_limit_raises_unavailable(self, mock_get, mock_oa):
        mock_get.side_effect = _http_error(429)
        mock_oa.side_effect = _http_error(429)
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
        assert 'Found 2 authors for "test"' in html


# ---------------------------------------------------------------------------
# OpenAlex fallback tests
# ---------------------------------------------------------------------------

_SAMPLE_OA_AUTHOR = {
    "id": "https://openalex.org/A5023888391",
    "display_name": "Yann LeCun",
    "display_name_alternatives": ["Y. LeCun"],
    "works_count": 450,
    "cited_by_count": 200000,
    "summary_stats": {"h_index": 120},
}

_SAMPLE_OA_WORK = {
    "id": "https://openalex.org/W123",
    "display_name": "Deep learning",
    "publication_year": 2015,
    "cited_by_count": 30000,
    "ids": {
        "openalex": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1038/x",
    },
    "primary_location": {"source": {"display_name": "Nature"}},
    "abstract_inverted_index": {"Deep": [0], "learning": [1], "works": [2]},
}


class TestOpenAlexFallback:
    """S2 429/failure should transparently fall back to OpenAlex."""

    @patch("services.paper.metadata.author_search._oa_get")
    @patch("services.paper.metadata.author_search._s2_get")
    def test_s2_429_falls_back_to_openalex(self, mock_s2, mock_oa):
        mock_s2.side_effect = _http_error(429)
        mock_oa.return_value = {"results": [_SAMPLE_OA_AUTHOR]}
        results = search_authors("Yann LeCun")
        assert len(results) == 1
        assert results[0].author_id == "A5023888391"
        assert results[0].name == "Yann LeCun"
        assert results[0].h_index == 120

    @patch("services.paper.metadata.author_search._oa_get")
    @patch("services.paper.metadata.author_search._s2_get")
    def test_both_down_raises_unavailable(self, mock_s2, mock_oa):
        mock_s2.side_effect = _http_error(429)
        mock_oa.side_effect = Exception("openalex down")
        with pytest.raises(S2UnavailableError):
            search_authors("Yann LeCun")

    @patch("services.paper.metadata.author_search._s2_get")
    @patch("services.paper.metadata.author_search._oa_get")
    def test_openalex_id_routes_to_openalex(self, mock_oa, mock_s2):
        """IDs like A5023888391 (from an OA-backed search) skip S2 entirely."""
        mock_oa.return_value = {"results": [_SAMPLE_OA_WORK]}
        papers = get_author_papers("A5023888391")
        mock_s2.assert_not_called()
        assert len(papers) == 1
        assert papers[0].title == "Deep learning"
        assert papers[0].external_ids.get("DOI") == "10.1038/x"

    @patch("services.paper.metadata.author_search._oa_get")
    def test_openalex_profile(self, mock_oa):
        def route(path, params=None, *, timeout=10):
            if "/authors/" in path:
                return _SAMPLE_OA_AUTHOR
            return {"results": [_SAMPLE_OA_WORK]}

        mock_oa.side_effect = route
        profile = get_author_profile("A5023888391")
        assert profile is not None
        assert profile.author.name == "Yann LeCun"
        assert len(profile.papers) == 1


class TestOpenAlexHelpers:
    def test_abstract_inverted_index_reconstruct(self):
        from services.paper.metadata.author_search import _oa_abstract

        assert _oa_abstract({"b": [1], "a": [0], "c": [2]}) == "a b c"
        assert _oa_abstract(None) is None
        assert _oa_abstract({}) is None

    def test_unavailable_renders_panel(self):
        from api.routes.author import _render_author_list

        html = _render_author_list([], "Test", unavailable=True)
        assert "temporarily unavailable" in html
        assert "No authors found" not in html


# ---------------------------------------------------------------------------
# Namesake disambiguation tests
# ---------------------------------------------------------------------------


class TestNamesakeDisambiguation:
    """S2 conflates namesakes into one entity; matching must require
    every query token, not just any single one."""

    def test_token_match_score_requires_all_tokens(self):
        from services.paper.metadata.author_search import _token_match_score

        a = AuthorResult(author_id="1", name="Vincent Sitzmann")
        assert _token_match_score(a, {"vincent", "sitzmann"}) == 2
        assert _token_match_score(a, {"vincent"}) == 1
        assert _token_match_score(a, {"vincent", "zitzmann"}) == 1

    def test_rank_prefers_full_name_match(self):
        from services.paper.metadata.author_search import rank_author_matches

        wrong = AuthorResult(
            author_id="1", name="Cathel Zitzmann", citation_count=99999
        )
        right = AuthorResult(author_id="2", name="Vincent Sitzmann", citation_count=10)
        ranked = rank_author_matches([wrong, right], "Vincent Sitzmann")
        assert ranked[0].name == "Vincent Sitzmann"

    def test_aliases_count_toward_match(self):
        from services.paper.metadata.author_search import all_tokens_match

        a = AuthorResult(author_id="1", name="V Sitzmann", aliases=["Vincent Sitzmann"])
        assert all_tokens_match(a, "Vincent Sitzmann") is True

    def test_paper_byline_filter_drops_namesakes(self):
        from services.paper.metadata.author_search import _paper_authors_match

        target = "Vincent Sitzmann"
        assert _paper_authors_match(["Vincent Sitzmann", "Thomas Leimkuhler"], target)
        assert _paper_authors_match(["V. Sitzmann", "Julien Martel"], target)
        assert not _paper_authors_match(
            ["Cathel Zitzmann", "Marion Jerome Sitzmann"], target
        )
        assert not _paper_authors_match(["F Sitzmann", "Vincent Herrmann"], target)
        assert not _paper_authors_match(["Franz Sitzmann"], target)
        # No byline data -> keep the paper rather than empty the list.
        assert _paper_authors_match([], target)

    def test_profile_filters_conflated_papers(self):
        """A conflated entity keeps only papers whose bylines match."""
        from services.paper.metadata.author_search import get_author_profile

        details = {
            "authorId": "1",
            "name": "Vincent Sitzmann",
            "aliases": [],
            "paperCount": 5,
            "citationCount": 100,
            "hIndex": 3,
        }
        papers = {
            "data": [
                {
                    "paperId": "p1",
                    "title": "Scene Representation Networks",
                    "authors": [
                        {"name": "Vincent Sitzmann"},
                        {"name": "Julien Martel"},
                    ],
                    "externalIds": {},
                },
                {
                    "paperId": "p2",
                    "title": "Unrelated namesake paper",
                    "authors": [{"name": "Franz Sitzmann"}],
                    "externalIds": {},
                },
            ]
        }

        with patch("services.paper.metadata.author_search._s2_get") as mock_get:
            mock_get.side_effect = [details, papers]
            profile = get_author_profile("1")
        assert profile is not None
        assert [p.title for p in profile.papers] == ["Scene Representation Networks"]

    def test_fully_conflated_profile_shows_no_papers(self):
        """When every byline mismatches, the profile must not keep them."""
        from services.paper.metadata.author_search import get_author_profile

        details = {
            "authorId": "1",
            "name": "Vincent Sitzmann",
            "aliases": [],
            "paperCount": 5,
            "citationCount": 100,
            "hIndex": 3,
        }
        papers = {
            "data": [
                {
                    "paperId": "p1",
                    "title": "Franz's paper",
                    "authors": [{"name": "Franz Sitzmann"}],
                    "externalIds": {},
                },
                {
                    "paperId": "p2",
                    "title": "Marion's paper",
                    "authors": [{"name": "Marion Jerome Sitzmann"}],
                    "externalIds": {},
                },
            ]
        }
        with patch("services.paper.metadata.author_search._s2_get") as mock_get:
            mock_get.side_effect = [details, papers]
            profile = get_author_profile("1")
        assert profile is not None
        assert profile.papers == []


class TestFeedAuthorName:
    def test_returns_name_not_tuple(self):
        from services.topics.feed import _author_name

        assert _author_name("papers by Yann LeCun") == "Yann LeCun"
        assert _author_name("Vincent Sitzmann") == "Vincent Sitzmann"
        assert _author_name("attention is all you need") == (
            "attention is all you need"
        )


class TestTopicKindDisambiguation:
    def test_bare_name_is_subject_without_confirmation(self):
        from services.topics.keys import detect_topic_kind

        assert detect_topic_kind("Neural Scaling Laws") == "subject"
        assert detect_topic_kind("Vincent Sitzmann") == "subject"
        assert detect_topic_kind("papers by Yann LeCun") == "author"
        assert detect_topic_kind("attention is all you need") == "subject"

    @patch("services.paper.metadata.author_search.search_authors")
    def test_confirmed_bare_name_is_author(self, mock_search):
        from services.topics.keys import detect_topic_kind

        mock_search.return_value = [AuthorResult(author_id="1", name="Yann LeCun")]
        assert detect_topic_kind("Yann LeCun", confirm_bare_name=True) == "author"

    @patch("services.paper.metadata.author_search.search_authors")
    def test_unconfirmed_bare_name_stays_subject(self, mock_search):
        from services.topics.keys import detect_topic_kind

        # No candidate covers every token -> topic, not a person.
        mock_search.return_value = [AuthorResult(author_id="1", name="Neural Tangents")]
        assert (
            detect_topic_kind("Neural Scaling Laws", confirm_bare_name=True)
            == "subject"
        )

    @patch("services.paper.metadata.author_search.search_authors")
    def test_author_service_down_stays_subject(self, mock_search):
        from services.topics.keys import detect_topic_kind

        mock_search.side_effect = Exception("S2 down")
        assert detect_topic_kind("Yann LeCun", confirm_bare_name=True) == "subject"

    def test_alias_byline_keeps_paper(self):
        from services.paper.metadata.author_search import _paper_authors_match

        assert _paper_authors_match(
            ["Alex Smith"], "Alexander Smith", aliases=["Alex Smith"]
        )

    def test_consistent_aliases_filter(self):
        from services.paper.metadata.author_search import _consistent_aliases

        aliases = ["V. Sitzmann", "V Sitzmann", "Franz Sitzmann", "Cathel Zitzmann"]
        kept = _consistent_aliases("Vincent Sitzmann", aliases)
        assert "Franz Sitzmann" not in kept
        assert "Cathel Zitzmann" not in kept
        assert "V. Sitzmann" in kept

    def test_namesake_alias_cannot_vouch_for_byline(self):
        """An alias belonging to a merged-in person must not pass the filter."""
        from services.paper.metadata.author_search import (
            _consistent_aliases,
            _paper_authors_match,
        )

        trusted = _consistent_aliases(
            "Vincent Sitzmann", ["Franz Sitzmann", "V. Sitzmann"]
        )
        assert not _paper_authors_match(
            ["Franz Sitzmann"], "Vincent Sitzmann", aliases=trusted
        )

    def test_subject_forced_overrides_author(self):
        """'Search as a topic' keeps even a real name a subject."""
        from services.topics.keys import detect_topic_kind

        assert (
            detect_topic_kind("Yann LeCun", confirm_bare_name=True, subject_forced=True)
            == "subject"
        )
        assert (
            detect_topic_kind("papers by Yann LeCun", subject_forced=True) == "subject"
        )
