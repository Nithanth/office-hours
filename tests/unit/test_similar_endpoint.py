"""Unit tests for the similar papers endpoint rendering."""

from __future__ import annotations

from models.paper import Paper


def _make_paper(**kwargs) -> Paper:
    defaults = {
        "id": "test1",
        "title": "Test Paper",
        "url": "https://arxiv.org/abs/2106.09685",
        "authors": ["Alice", "Bob"],
        "citation_count": 42,
    }
    defaults.update(kwargs)
    return Paper(**defaults)


class TestRenderSimilarFragment:
    """Tests for the HTML fragment rendering."""

    def test_renders_valid_html(self):
        from api.routes.similar import _render_similar_fragment

        papers = [_make_paper(id=f"p{i}", title=f"Paper {i}") for i in range(3)]
        result = _render_similar_fragment(papers, anchor_title="Original Paper")

        assert "similar-panel" in result
        assert "Original Paper" in result
        assert "Paper 0" in result
        assert "Paper 1" in result
        assert "Paper 2" in result

    def test_includes_checkboxes(self):
        from api.routes.similar import _render_similar_fragment

        papers = [_make_paper()]
        result = _render_similar_fragment(papers, anchor_title="Test")

        assert "selected_papers_json" in result
        assert "checkbox" in result

    def test_shows_citation_count(self):
        from api.routes.similar import _render_similar_fragment

        papers = [_make_paper(citation_count=1500)]
        result = _render_similar_fragment(papers, anchor_title="Test")

        assert "1,500" in result

    def test_shows_authors_compact(self):
        from api.routes.similar import _render_similar_fragment

        papers = [_make_paper(authors=["Alice", "Bob", "Charlie", "Diana", "Eve"])]
        result = _render_similar_fragment(papers, anchor_title="Test")

        assert "Alice" in result
        assert "et al." in result

    def test_paper_json_in_checkbox_value(self):
        from api.routes.similar import _render_similar_fragment

        paper = _make_paper(title="Special & Paper <Title>")
        result = _render_similar_fragment([paper], anchor_title="Test")

        # The paper JSON should be HTML-escaped in the value attribute
        assert "value='" in result


class TestErrorFragment:
    """Tests for error rendering."""

    def test_error_message_displayed(self):
        from api.routes.similar import _error_fragment

        result = _error_fragment("Something went wrong")
        assert "Something went wrong" in result
        assert "similar-panel" in result

    def test_html_escaped(self):
        from api.routes.similar import _error_fragment

        result = _error_fragment("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
