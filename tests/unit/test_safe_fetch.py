"""SSRF guard tests: every hop and every DNS answer must be public."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from fastapi import FastAPI

from api.routers import chat_modal
from services.paper.text_extractor import TextExtractor
from utils import safe_fetch
from utils.safe_fetch import (
    ResponseTooLargeError,
    UnsafeURLError,
    check_url,
    read_capped,
    safe_get,
    safe_session,
)

_FAKE_DNS = {"public.test": "127.0.0.1", "internal.test": "10.0.0.5"}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/healthz",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://localhost:8000/",
        "http://metadata.google.internal/",
        "http://db.railway.internal/",
        "file:///etc/passwd",
        "ftp://example.com/x.pdf",
        "http:///nohost",
    ],
)
def test_check_url_rejects_non_public(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        check_url(url)


@pytest.mark.parametrize(
    "url", ["https://arxiv.org/pdf/2401.00001", "http://8.8.8.8/x.pdf"]
)
def test_check_url_allows_public(url: str) -> None:
    check_url(url)


@pytest.fixture()
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve *.test names offline and treat only 127.0.0.1 as "public".

    The real test server lives on 127.0.0.1, so that one address stands in
    for the public internet; every other private/loopback address stays blocked.
    """

    async def _resolve(self, host, port=0, family=socket.AF_INET):  # noqa: ANN001
        if host not in _FAKE_DNS:
            raise OSError(f"unknown host {host}")
        return [
            {
                "hostname": host,
                "host": _FAKE_DNS[host],
                "port": port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    monkeypatch.setattr(aiohttp.ThreadedResolver, "resolve", _resolve)
    real_is_public = safe_fetch.is_public_ip
    monkeypatch.setattr(
        safe_fetch,
        "is_public_ip",
        lambda ip: str(ip) == "127.0.0.1" or real_is_public(ip),
    )


@pytest.fixture()
async def upstream(fake_dns: None) -> AsyncIterator[str]:
    async def ok(_req: web.Request) -> web.Response:
        return web.Response(body=b"%PDF-1.4 ok", content_type="application/pdf")

    def redirect(target: str):
        async def _h(_req: web.Request) -> web.Response:
            raise web.HTTPFound(target.format(port=server.port))

        return _h

    async def big(_req: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        await resp.prepare(_req)
        for _ in range(64):
            await resp.write(b"x" * 65536)
        return resp

    async def scraped(_req: web.Request) -> web.Response:
        return web.Response(
            text='<a href="http://169.254.169.254/paper.pdf">pdf</a>',
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/ok.pdf", ok)
    app.router.add_get("/to-ok", redirect("/ok.pdf"))
    app.router.add_get("/to-loopback", redirect("http://127.0.0.2:{port}/ok.pdf"))
    app.router.add_get("/to-metadata", redirect("http://169.254.169.254/latest/"))
    app.router.add_get("/to-internal", redirect("http://internal.test:{port}/ok.pdf"))
    app.router.add_get("/big", big)
    app.router.add_get("/scraped", scraped)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        yield f"http://public.test:{server.port}"
    finally:
        await server.close()


async def test_public_redirect_followed(upstream: str) -> None:
    async with safe_session() as s, safe_get(s, f"{upstream}/to-ok") as resp:
        assert resp.status == 200
        assert await resp.read() == b"%PDF-1.4 ok"


@pytest.mark.parametrize("path", ["/to-loopback", "/to-metadata", "/to-internal"])
async def test_redirect_to_internal_rejected(upstream: str, path: str) -> None:
    async with safe_session() as s:
        with pytest.raises(UnsafeURLError):
            async with safe_get(s, f"{upstream}{path}"):
                pass


async def test_hostname_resolving_private_rejected(upstream: str) -> None:
    port = upstream.rsplit(":", 1)[1]
    async with safe_session() as s:
        with pytest.raises(UnsafeURLError):
            async with safe_get(s, f"http://internal.test:{port}/ok.pdf"):
                pass


async def test_body_cap_enforced_without_content_length(upstream: str) -> None:
    async with safe_session() as s, safe_get(s, f"{upstream}/big") as resp:
        with pytest.raises(ResponseTooLargeError):
            await read_capped(resp, 1024 * 1024)


@pytest.fixture()
async def proxy_client(upstream: str) -> AsyncIterator[httpx.AsyncClient]:
    chat_modal._PROXY_SESSION = None
    app = FastAPI()
    app.include_router(chat_modal.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as c:
        yield c
    if chat_modal._PROXY_SESSION is not None:
        await chat_modal._PROXY_SESSION.close()
    chat_modal._PROXY_SESSION = None


async def test_pdf_proxy_streams_public(
    proxy_client: httpx.AsyncClient, upstream: str
) -> None:
    r = await proxy_client.get("/pdf-proxy", params={"url": f"{upstream}/ok.pdf"})
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 ok"
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("path", ["/to-loopback", "/to-metadata", "/to-internal"])
async def test_pdf_proxy_blocks_redirect_to_internal(
    proxy_client: httpx.AsyncClient, upstream: str, path: str
) -> None:
    r = await proxy_client.get("/pdf-proxy", params={"url": f"{upstream}{path}"})
    assert r.status_code == 403


async def test_pdf_proxy_blocks_private_dns(
    proxy_client: httpx.AsyncClient, upstream: str
) -> None:
    port = upstream.rsplit(":", 1)[1]
    url = f"http://internal.test:{port}/ok.pdf"
    r = await proxy_client.get("/pdf-proxy", params={"url": url})
    assert r.status_code == 403


async def test_html_proxy_blocks_redirect_to_internal(
    proxy_client: httpx.AsyncClient,
    upstream: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_modal,
        "_ALLOWED_PROXY_DOMAINS",
        {*chat_modal._ALLOWED_PROXY_DOMAINS, "public.test"},
    )
    r = await proxy_client.get("/html-proxy", params={"url": f"{upstream}/to-loopback"})
    assert r.status_code == 403


async def test_extractor_html_layer_rejects_redirect(upstream: str) -> None:
    with pytest.raises(UnsafeURLError):
        await TextExtractor()._layer_html(f"{upstream}/to-metadata")


async def test_extractor_skips_internal_scraped_pdf_link(upstream: str) -> None:
    assert await TextExtractor()._layer_html_pdf_link(f"{upstream}/scraped") is None


async def test_ensure_cached_refuses_loopback_paper_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("services.paper.text_extractor.CACHE_ROOT", tmp_path)
    result = await TextExtractor().ensure_cached(
        "http://127.0.0.1:8000/healthz", paper_id="p-ssrf", prefer_exa=False
    )
    assert result is None
    assert not list(tmp_path.iterdir())
