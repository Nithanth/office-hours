"""Unit tests for Semantic Scholar citation enrichment."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from models.paper import Paper
from services.paper.metadata.semantic_scholar import (
    _fetch_batch,
    batch_fetch_citations,
    batch_fetch_paper_meta,
    enrich_papers_with_citations,
)


def _meta(citations=None, arxiv_id=None, doi=None):
    return {"citations": citations, "arxiv_id": arxiv_id, "doi": doi}


class TestBatchFetchCitations:
    """Tests for batch_fetch_citations."""

    def test_empty_input(self):
        result = batch_fetch_citations([])
        assert result == {}

    @patch("services.paper.metadata.semantic_scholar._fetch_batch")
    def test_single_batch(self, mock_fetch):
        mock_fetch.return_value = {"ARXIV:2106.09685": _meta(citations=5000)}
        result = batch_fetch_citations(["ARXIV:2106.09685"])
        assert result == {"ARXIV:2106.09685": 5000}
        mock_fetch.assert_called_once_with(["ARXIV:2106.09685"], timeout=10)

    @patch("services.paper.metadata.semantic_scholar._fetch_batch")
    def test_multiple_batches(self, mock_fetch):
        """IDs exceeding 500 get split into multiple batches."""
        ids = [f"ARXIV:{i}" for i in range(750)]
        mock_fetch.return_value = {}
        batch_fetch_citations(ids, timeout=5)
        # Should have been called twice: 500 + 250
        assert mock_fetch.call_count == 2
        first_call_ids = mock_fetch.call_args_list[0][0][0]
        second_call_ids = mock_fetch.call_args_list[1][0][0]
        assert len(first_call_ids) == 500
        assert len(second_call_ids) == 250


class TestFetchBatch:
    """Tests for _fetch_batch (mocking urllib)."""

    @patch("services.paper.metadata.semantic_scholar.urllib.request.urlopen")
    def test_successful_fetch(self, mock_urlopen):
        import io

        mock_urlopen.return_value.__enter__.return_value = io.BytesIO(
            json.dumps(
                [
                    {
                        "paperId": "abc123",
                        "citationCount": 42,
                        "externalIds": {"ArXiv": "2106.09685", "DOI": "10.1/x"},
                    },
                    None,  # paper not found
                    {"paperId": "def456", "citationCount": 7, "externalIds": {}},
                ]
            ).encode()
        )
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        ids = ["ARXIV:2106.09685", "ARXIV:9999.99999", "DOI:10.1234/test"]
        result = _fetch_batch(ids, timeout=5)
        # First and third resolve, second (None) is skipped
        assert result["ARXIV:2106.09685"]["citations"] == 42
        assert result["ARXIV:2106.09685"]["arxiv_id"] == "2106.09685"
        assert result["ARXIV:2106.09685"]["doi"] == "10.1/x"
        assert "ARXIV:9999.99999" not in result
        assert result["DOI:10.1234/test"]["citations"] == 7
        assert result["DOI:10.1234/test"]["arxiv_id"] is None

    @patch("services.paper.metadata.semantic_scholar.urllib.request.urlopen")
    def test_timeout_returns_empty(self, mock_urlopen):
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("timeout")
        result = _fetch_batch(["ARXIV:2106.09685"], timeout=1)
        assert result == {}

    @patch("services.paper.metadata.semantic_scholar._fetch_batch")
    def test_batch_fetch_paper_meta_preserves_ids(self, mock_fetch):
        mock_fetch.return_value = {
            "DOI:10.1145/x": _meta(citations=100, arxiv_id="2301.00001")
        }
        result = batch_fetch_paper_meta(["DOI:10.1145/x"])
        assert result["DOI:10.1145/x"]["arxiv_id"] == "2301.00001"


class TestEnrichPapersWithCitations:
    """Tests for enrich_papers_with_citations."""

    def _make_paper(self, url: str, pid: str = "test") -> Paper:
        return Paper(id=pid, title="Test", url=url)

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_arxiv_id_resolution(self, mock_batch):
        """arXiv URLs should be resolved to ARXIV: identifiers."""
        mock_batch.return_value = {"ARXIV:2106.09685": _meta(citations=5000)}
        papers = [self._make_paper("https://arxiv.org/abs/2106.09685")]
        enrich_papers_with_citations(papers)
        # Verify the paper was enriched
        assert papers[0].citation_count == 5000
        # Verify the correct ID was passed
        mock_batch.assert_called_once()
        called_ids = mock_batch.call_args[0][0]
        assert "ARXIV:2106.09685" in called_ids

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_url_fallback(self, mock_batch):
        """Non-arXiv URLs should fall back to URL: identifiers."""
        mock_batch.return_value = {"URL:https://example.com/paper": _meta(citations=10)}
        papers = [self._make_paper("https://example.com/paper")]
        enrich_papers_with_citations(papers)
        assert papers[0].citation_count == 10

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_unresolved_papers_stay_none(self, mock_batch):
        """Papers not found in S2 keep citation_count=None."""
        mock_batch.return_value = {}
        papers = [self._make_paper("https://example.com/paper")]
        enrich_papers_with_citations(papers)
        assert papers[0].citation_count is None

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_empty_papers_list(self, mock_batch):
        """Empty paper list shouldn't call the API."""
        enrich_papers_with_citations([])
        mock_batch.assert_not_called()

    @patch("services.paper.metadata.semantic_scholar.urllib.request.urlopen")
    def test_graceful_on_api_error(self, mock_urlopen):
        """API errors shouldn't crash enrichment."""
        mock_urlopen.side_effect = URLError("Network error")
        papers = [self._make_paper("https://arxiv.org/abs/2106.09685")]
        enrich_papers_with_citations(papers)
        assert papers[0].citation_count is None


class TestUrlCanonicalization:
    """Publisher links with an S2-known arXiv version get rewritten."""

    def _make_paper(self, url: str, pid: str | None = None) -> Paper:
        return Paper(id=pid or url, title="Test", url=url)

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_acm_url_rewritten_to_arxiv(self, mock_batch):
        """dl.acm.org DOI → arxiv.org/abs when S2 knows the ArXiv id."""
        acm = "https://dl.acm.org/doi/10.1145/3447548.3467171"
        mock_batch.return_value = {
            "DOI:10.1145/3447548.3467171": _meta(citations=50, arxiv_id="2006.09661")
        }
        papers = [self._make_paper(acm)]
        enrich_papers_with_citations(papers)
        assert str(papers[0].url) == "https://arxiv.org/abs/2006.09661"
        assert papers[0].id == "https://arxiv.org/abs/2006.09661"
        assert papers[0].citation_count == 50

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_open_access_url_kept(self, mock_batch):
        """ACL Anthology links are already readable — leave them alone."""
        acl = "https://aclanthology.org/2020.acl-main.123/"
        mock_batch.return_value = {
            "URL:" + acl: _meta(citations=9, arxiv_id="2005.00001")
        }
        papers = [self._make_paper(acl)]
        enrich_papers_with_citations(papers)
        assert str(papers[0].url) == acl

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_no_arxiv_version_keeps_url(self, mock_batch):
        """Publisher links with no arXiv mapping stay publisher links."""
        acm = "https://dl.acm.org/doi/10.1145/nopreprint"
        mock_batch.return_value = {
            "DOI:10.1145/nopreprint": _meta(citations=3, arxiv_id=None)
        }
        papers = [self._make_paper(acm)]
        enrich_papers_with_citations(papers)
        assert str(papers[0].url) == acm

    @patch("services.paper.metadata.semantic_scholar.batch_fetch_paper_meta")
    def test_canonicalization_dedupes_twins(self, mock_batch):
        """An ACM entry and its arXiv twin collapse into one result."""
        acm = "https://dl.acm.org/doi/10.1145/3447548.3467171"
        arxiv = "https://arxiv.org/abs/2006.09661"
        mock_batch.return_value = {
            "DOI:10.1145/3447548.3467171": _meta(citations=50, arxiv_id="2006.09661"),
            "ARXIV:2006.09661": _meta(citations=50, arxiv_id="2006.09661"),
        }
        papers = [self._make_paper(acm), self._make_paper(arxiv)]
        enrich_papers_with_citations(papers)
        assert len(papers) == 1
        assert "arxiv.org" in str(papers[0].url)
