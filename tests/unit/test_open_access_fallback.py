import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urljoin

import aiohttp
import pytest

import config as config_module
from api.routers import chat_modal
from services.paper import open_access
from services.paper.text_extractor import TextExtractor


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    open_access._candidates.cache_clear()
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", None)
    monkeypatch.setattr(config_module.config, "SEMANTIC_SCHOLAR_API_KEY", None)
    monkeypatch.setattr(open_access.config, "OPENALEX_MAILTO", None)
    monkeypatch.setattr(
        open_access.urllib.request, "urlopen", Mock(side_effect=OSError())
    )


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


@pytest.mark.parametrize("key", [None, "test-key"])
async def test_public_s2_lookup_and_optional_key(monkeypatch, key):
    monkeypatch.setattr(open_access.config, "SEMANTIC_SCHOLAR_API_KEY", key)
    monkeypatch.setattr(config_module.config, "SEMANTIC_SCHOLAR_API_KEY", key)
    payload = {"externalIds": {"ArXiv": "2401.12345"}}
    fetch = Mock(return_value=io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(open_access.urllib.request, "urlopen", fetch)
    assert await open_access.open_copy_candidates("https://doi.org/10.1234/test") == [
        "https://arxiv.org/pdf/2401.12345.pdf"
    ]
    assert fetch.call_args.args[0].get_header("X-api-key") == key


async def test_source_pane_preserves_sandbox_and_fallback(monkeypatch):
    monkeypatch.setattr(chat_modal, "_is_public_http_url", lambda _: True)
    proxy = AsyncMock(return_value=chat_modal.HTMLResponse("<h1>Paper</h1>"))
    monkeypatch.setattr(chat_modal, "html_proxy", proxy)
    result = await chat_modal.source_pane(None, "https://publisher.test/article")
    assert 'src="/html-proxy?' in result.body.decode()
    assert "srcdoc=" not in result.body.decode()
    assert (
        'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"'
        in result.body.decode()
    )
    fallback = chat_modal.HTMLResponse("Open at source", headers={"X-Open-Copy": "1"})
    proxy.return_value = fallback
    assert (
        await chat_modal.source_pane(None, "https://publisher.test/article") is fallback
    )


@pytest.mark.parametrize(
    "head",
    [
        "<html><head></head><body>",
        "",
        '<head><base href="https://assets.test/papers/"></head>',
    ],
)
async def test_html_proxy_relative_resources_and_csp(monkeypatch, head):
    upstream = response()
    upstream.url = "https://publisher.test/papers/article.html"
    monkeypatch.setattr(chat_modal, "_is_url_allowed", lambda _: True)
    monkeypatch.setattr(chat_modal, "_get_proxy_session", lambda: None)
    monkeypatch.setattr(chat_modal, "open_get", AsyncMock(return_value=upstream))
    document = (
        head
        + '<a href="related.html">Related</a><img src="figures/1.png"><link rel="stylesheet" href="style.css"><script>alert(1)</script>'
    )
    monkeypatch.setattr(chat_modal, "text_capped", AsyncMock(return_value=document))
    result = await chat_modal.html_proxy(None, "https://publisher.test/original")
    body = result.body.decode()
    base = re.search(r'<base href="([^"]+)"', body).group(1)
    expected = (
        "https://assets.test/papers/"
        if "<base" in head
        else "https://publisher.test/papers/"
    )
    for relative in ("related.html", "figures/1.png", "style.css"):
        assert urljoin(base, relative) == expected + relative
    assert body.count("<base") == 1
    assert "script-src 'none'" in result.headers["Content-Security-Policy"]
    assert result.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_paywall_detection_uses_short_visible_text():
    assert open_access.is_paywall("Access denied")
    assert not open_access.is_paywall("access denied " * 1000)
    assert open_access.is_paywall(
        "<script>" + "x" * 10000 + "</script><p>Access denied</p>",
        max_chars=2000,
        is_html=True,
    )
    assert not open_access.is_paywall(
        "<p>" + "Paper text " * 1000 + " access denied</p>",
        max_chars=2000,
        is_html=True,
    )
    assert not open_access.is_paywall(
        '<script>"access denied"</script><p>Paper</p>', is_html=True
    )


def response(status=200, body=b"%PDF-1.4 test"):
    return SimpleNamespace(
        status=status,
        content=SimpleNamespace(read=AsyncMock(return_value=body)),
        release=Mock(),
        headers={},
    )


@pytest.mark.parametrize(
    "marker",
    [
        "Just a moment...",
        "Verifying you are human",
        "Verifying your browser",
        "Complete the check below",
        "Enable JavaScript and cookies to continue",
        '<div class="cf-turnstile" data-sitekey="public-test-key"></div>',
        '<div class="g-recaptcha"></div>',
        '<div id="captcha-container"></div>',
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
    ],
)
def test_short_browser_challenge_detection(marker):
    assert open_access.is_paywall(marker, max_chars=2000, is_html=True)
    assert not open_access.is_paywall(
        marker + "<p>" + "Security research content. " * 200 + "</p>",
        max_chars=2000,
        is_html=True,
    )


async def test_source_pane_falls_back_from_browser_challenge(monkeypatch):
    challenge = """<!DOCTYPE html><html><head><title>Just a moment...</title>
    <style>.main-content { margin: 8rem auto; }</style></head><body>
    <main class="main-content"><h1>openreview.net</h1>
    <h2>Verifying your browser</h2><p>Complete the check below</p>
    <div class="cf-turnstile" data-sitekey="public-test-key"></div>
    <noscript>Enable JavaScript and cookies to continue</noscript></main>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
    </body></html>"""
    source = "https://openreview.net/forum?id=YicbFdNTTy"
    monkeypatch.setattr(chat_modal, "_is_url_allowed", lambda _: True)
    monkeypatch.setattr(chat_modal, "_is_public_http_url", lambda _: True)
    monkeypatch.setattr(chat_modal, "_get_proxy_session", lambda: None)
    monkeypatch.setattr(chat_modal, "open_get", AsyncMock(return_value=response()))
    monkeypatch.setattr(chat_modal, "text_capped", AsyncMock(return_value=challenge))
    monkeypatch.setattr(chat_modal, "open_copy_candidates", AsyncMock(return_value=[]))
    result = await chat_modal.source_pane(None, source)
    body = result.body.decode()
    assert "Open at source" in body
    assert f'href="{source}"' in body
    assert "<iframe" not in body


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


async def test_extractor_keeps_long_security_paper(monkeypatch, tmp_path):
    monkeypatch.setattr("services.paper.text_extractor.CACHE_ROOT", tmp_path)
    extractor = TextExtractor()
    text = "This paper studies access denied and 403 forbidden responses. " * 1000
    monkeypatch.setattr(extractor, "_layer_arxiv_pdf", AsyncMock(return_value=None))
    monkeypatch.setattr(extractor, "_layer_pdf", AsyncMock(return_value=text))
    fallback = AsyncMock()
    monkeypatch.setattr(open_access, "open_copy_candidates", fallback)
    path = await extractor.ensure_cached(
        "https://publisher.test/paper", "security-paper"
    )
    assert path and Path(path).read_text() == text
    fallback.assert_not_awaited()
