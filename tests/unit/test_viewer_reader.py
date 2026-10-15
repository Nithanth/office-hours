"""Unit tests for the viewer reader modes (Source / ar5iv HTML / Text tabs)."""

from __future__ import annotations

import urllib.parse


def _viewer_url(paper_id: str, url: str) -> str:
    return (
        f"/viewer/{urllib.parse.quote(paper_id, safe='')}"
        f"?url={urllib.parse.quote_plus(url)}"
    )


class TestViewerTabs:
    def test_arxiv_gets_html_and_text_tabs(self, client):
        pid = "https://arxiv.org/abs/2401.0001"
        resp = client.get(_viewer_url(pid, "https://arxiv.org/abs/2401.0001"))
        assert resp.status_code == 200
        assert 'class="viewer-tabs"' in resp.text
        assert "ar5iv.labs.arxiv.org" in resp.text
        assert "2401.0001" in resp.text
        assert ">HTML</button>" in resp.text
        assert ">Text</button>" in resp.text
        assert 'id="doc-pane-slot"' in resp.text

    def test_non_arxiv_omits_html_tab(self, client):
        pid = "https://doi.org/10.1234/x"
        resp = client.get(_viewer_url(pid, "https://doi.org/10.1234/x.pdf"))
        assert resp.status_code == 200
        assert 'class="viewer-tabs"' in resp.text
        assert ">HTML</button>" not in resp.text
        assert ">PDF</button>" in resp.text
        assert ">Text</button>" in resp.text


class _FakeExtractor:
    text: str | None = None

    def __init__(self, text: str | None):
        self._text = text

    async def read_full_text(self, paper_id: str):
        return self._text

    async def ensure_cached(self, url, paper_id=None, **kw):
        return None


class TestTextPane:
    def test_renders_cached_text(self, client, monkeypatch):
        from services.paper.text_extractor import TextExtractor

        monkeypatch.setattr(
            TextExtractor,
            "instance",
            classmethod(lambda cls: _FakeExtractor("Section 1\nIntro text")),
        )
        resp = client.get(
            "/text-pane?paper_id="
            + urllib.parse.quote_plus("https://arxiv.org/abs/2401.0001")
        )
        assert resp.status_code == 200
        assert "paper-text-body" in resp.text
        assert "Section 1" in resp.text

    def test_escapes_html_in_text(self, client, monkeypatch):
        from services.paper.text_extractor import TextExtractor

        monkeypatch.setattr(
            TextExtractor,
            "instance",
            classmethod(lambda cls: _FakeExtractor("<script>alert(1)</script>")),
        )
        resp = client.get("/text-pane?paper_id=p1")
        assert "<script>" not in resp.text
        assert "alert(1)" in resp.text  # escaped, still visible as text

    def test_no_text_renders_honest_empty(self, client, monkeypatch):
        from services.paper.text_extractor import TextExtractor

        monkeypatch.setattr(
            TextExtractor,
            "instance",
            classmethod(lambda cls: _FakeExtractor(None)),
        )
        resp = client.get("/text-pane?paper_id=p1")
        assert resp.status_code == 200
        assert "isn't available" in resp.text

    def test_disallowed_url_skips_extraction(self, client, monkeypatch):
        from services.paper.text_extractor import TextExtractor

        calls: list[str] = []

        class _Spying(_FakeExtractor):
            async def ensure_cached(self, url, paper_id=None, **kw):
                calls.append(url)

        monkeypatch.setattr(
            TextExtractor,
            "instance",
            classmethod(lambda cls: _Spying(None)),
        )
        resp = client.get(
            "/text-pane?paper_id=p1&url="
            + urllib.parse.quote_plus("https://evil.example.com/x.pdf")
        )
        assert resp.status_code == 200
        assert "isn't available" in resp.text
        assert calls == []
