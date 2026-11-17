"""Chat modal opened with only a paper id must show the cached paper title.

Callers like the session-restore in ``main.js`` and the viewer's no-``url``
fallback pass no ``title``; the modal then relies on the paper service's
cached metadata instead of showing the raw id.
"""

from __future__ import annotations

import urllib.parse

import api.routers.chat_modal as chat_modal_mod
from services.paper.text_extractor import TextExtractor

_PID = "https://arxiv.org/abs/1706.03762"


def _extractor_with_metadata(meta: dict | None) -> TextExtractor:
    extractor = object.__new__(TextExtractor)

    async def _load_metadata(paper_id: str):
        assert paper_id == _PID
        return meta

    extractor.load_metadata = _load_metadata  # type: ignore[method-assign]
    return extractor


def _open(client):
    return client.get(f"/chat-modal?paper_id={urllib.parse.quote(_PID, safe='')}")


def test_chat_modal_uses_cached_paper_title(client, monkeypatch):
    extractor = _extractor_with_metadata({"title": "Attention Is All You Need"})
    monkeypatch.setattr(chat_modal_mod, "get_paper_service", lambda: extractor)

    resp = _open(client)

    assert resp.status_code == 200
    assert "Attention Is All You Need" in resp.text
    assert f">{_PID}</h2>" not in resp.text


def test_chat_modal_falls_back_to_id_without_metadata(client, monkeypatch):
    extractor = _extractor_with_metadata(None)
    monkeypatch.setattr(chat_modal_mod, "get_paper_service", lambda: extractor)

    resp = _open(client)

    assert resp.status_code == 200
    assert _PID in resp.text
