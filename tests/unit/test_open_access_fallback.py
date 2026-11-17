import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from api.routers import chat_modal
from services.paper import open_access
from services.paper.text_extractor import TextExtractor


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    open_access._candidates.cache_clear()
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", None)
    monkeypatch.setattr(open_access.config, "OPENALEX_MAILTO", None)


async def test_order_dedupe_and_failed(monkeypatch):
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", "test")
    monkeypatch.setattr(open_access.config, "OPENALEX_MAILTO", "test@example.org")
    payload = {
        "externalIds": {"ArXiv": "2401.12345", "DOI": "10.1234/test"},
        "openAccessPdf": {"url": "https://oa.test/paper.pdf"},
    }
    fetch = Mock(side_effect=lambda *a, **k: io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(open_access.urllib.request, "urlopen", fetch)
    monkeypatch.setattr(
        open_access,
        "resolve_open_access_url",
        lambda *a, **k: "https://repo.test/paper.pdf",
    )
    source = "https://openreview.net/forum?id=abc"
    expected = [
        "https://arxiv.org/pdf/2401.12345.pdf",
        "https://openreview.net/pdf?id=abc",
        "https://repo.test/paper.pdf",
        "https://oa.test/paper.pdf",
    ]
    assert await open_access.open_copy_candidates(source) == expected
    assert (
        await open_access.open_copy_candidates(source, failed=(expected[0],))
        == expected[1:]
    )
    assert fetch.call_count == 1
    open_access._candidates.cache_clear()
    payload["openAccessPdf"]["url"] = expected[0]
    assert await open_access.open_copy_candidates(source) == expected[:-1]


async def test_openreview_without_keys():
    assert await open_access.open_copy_candidates(
        "https://arxiv.org/abs/2401.12345"
    ) == ["https://arxiv.org/pdf/2401.12345.pdf"]
    assert await open_access.open_copy_candidates(
        "https://openreview.net/forum?id=a%2Fb"
    ) == ["https://openreview.net/pdf?id=a%2Fb"]
    assert (
        await open_access.open_copy_candidates("https://openreview.net/pdf?id=abc")
        == []
    )
    assert await open_access.open_copy_candidates("https://publisher.test/paper") == []


async def test_lookup_error(monkeypatch):
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", "test")
    monkeypatch.setattr(
        open_access.urllib.request, "urlopen", Mock(side_effect=OSError())
    )
    assert await open_access.open_copy_candidates(
        "https://openreview.net/forum?id=abc"
    ) == ["https://openreview.net/pdf?id=abc"]


async def test_title_mapping(monkeypatch):
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", "test")
    payload = {"data": [{"externalIds": {"ArXiv": "2401.12345"}}]}
    fetch = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(open_access.urllib.request, "urlopen", fetch)
    assert await open_access.open_copy_candidates(
        "https://publisher.test/article", "Paper title"
    ) == ["https://arxiv.org/pdf/2401.12345.pdf"]
    assert "search/match?" in fetch.call_args.args[0].full_url
    assert "Paper+title" in fetch.call_args.args[0].full_url


async def test_source_pane_preserves_sandbox_and_fallback(monkeypatch):
    monkeypatch.setattr(chat_modal, "_is_public_http_url", lambda _: True)
    proxy = AsyncMock(return_value=chat_modal.HTMLResponse("<h1>Paper</h1>"))
    monkeypatch.setattr(chat_modal, "html_proxy", proxy)
    result = await chat_modal.source_pane(None, "https://publisher.test/article")
    assert "srcdoc=" in result.body.decode()
    assert (
        'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"'
        in result.body.decode()
    )
    fallback = chat_modal.HTMLResponse("Open at source", headers={"X-Open-Copy": "1"})
    proxy.return_value = fallback
    assert (
        await chat_modal.source_pane(None, "https://publisher.test/article") is fallback
    )


def response(status=200, body=b"%PDF-1.4 test"):
    return SimpleNamespace(
        status=status,
        content=SimpleNamespace(read=AsyncMock(return_value=body)),
        release=Mock(),
        headers={},
    )


async def test_viewer_skips_failed_candidate(monkeypatch):
    monkeypatch.setattr(chat_modal, "_is_public_http_url", lambda _: True)
    monkeypatch.setattr(chat_modal, "_get_proxy_session", lambda: None)
    monkeypatch.setattr(
        chat_modal,
        "open_copy_candidates",
        AsyncMock(return_value=["https://a.test/a.pdf", "https://b.test/b.pdf"]),
    )
    fetch = AsyncMock(side_effect=[response(403), response()])
    monkeypatch.setattr(chat_modal, "open_get", fetch)
    result = await chat_modal.open_copy_pane(None, "https://original.test/paper")
    assert "b.test" in result.body.decode()
    assert 'data-original-url="https://original.test/paper"' in result.body.decode()
    assert fetch.await_count == 2


async def test_all_fail_source_link(monkeypatch):
    monkeypatch.setattr(chat_modal, "_is_public_http_url", lambda _: True)
    monkeypatch.setattr(chat_modal, "_get_proxy_session", lambda: None)
    monkeypatch.setattr(
        chat_modal,
        "open_copy_candidates",
        AsyncMock(return_value=["https://a.test/a.pdf"]),
    )
    monkeypatch.setattr(
        chat_modal, "open_get", AsyncMock(side_effect=aiohttp.ClientError())
    )
    result = await chat_modal.open_copy_pane(None, "https://original.test/paper")
    body = result.body.decode()
    assert 'href="https://original.test/paper"' in body
    assert 'target="_blank" rel="noopener noreferrer">Open at source' in body


@pytest.mark.parametrize(
    "status,body",
    [
        (403, "Forbidden"),
        (404, "Missing"),
        (500, "Error"),
        (200, "Sign in to access this article"),
    ],
)
async def test_html_proxy_fallback(monkeypatch, status, body):
    monkeypatch.setattr(chat_modal, "_is_url_allowed", lambda _: True)
    monkeypatch.setattr(chat_modal, "_get_proxy_session", lambda: None)
    monkeypatch.setattr(
        chat_modal, "open_get", AsyncMock(return_value=response(status))
    )
    monkeypatch.setattr(chat_modal, "text_capped", AsyncMock(return_value=body))
    fallback = AsyncMock(return_value=chat_modal.HTMLResponse("Open at source"))
    monkeypatch.setattr(chat_modal, "open_copy_pane", fallback)
    result = await chat_modal.html_proxy(None, "https://openreview.net/forum?id=abc")
    assert result.body == b"Open at source"
    fallback.assert_awaited_once()


async def test_extractor_uses_open_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr("services.paper.text_extractor.CACHE_ROOT", tmp_path)
    extractor = TextExtractor()
    for name in (
        "_layer_arxiv_pdf",
        "_layer_html_pdf_link",
        "_layer_exa_contents",
        "_layer_arxiv",
        "_layer_html",
    ):
        monkeypatch.setattr(extractor, name, AsyncMock(return_value=None))
    pdf = AsyncMock(side_effect=[None, "Full paper " * 1000])
    monkeypatch.setattr(extractor, "_layer_pdf", pdf)
    monkeypatch.setattr(
        open_access,
        "open_copy_candidates",
        AsyncMock(return_value=["https://oa.test/paper.pdf"]),
    )
    path = await extractor.ensure_cached("https://publisher.test/paper", "paper")
    assert path and Path(path).read_text().startswith("Full paper")
    assert pdf.await_args.args == ("https://oa.test/paper.pdf",)
