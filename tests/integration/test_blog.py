"""Integration tests for the /generate-blog flow.

Covers the new two-step blog pipeline:
  1. POST /generate-blog        – queues an in-memory generation, returns token
  2. GET  /blog-status?token=…  – polled until state=="ready"
  3. GET  /blog-view?token=…    – serves the rendering shell
  4. GET  /blog-view stays alive across renders (no pop-on-view 404).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _wait_for_ready(
    client: TestClient, token: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Poll /blog-status until ready/error/expired or timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get("/blog-status", params={"token": token})
        assert resp.status_code in (200, 404), resp.status_code
        body: dict[str, Any] = resp.json()
        state = body.get("state")
        if state in {"ready", "error", "expired"}:
            return body
        time.sleep(0.05)
    pytest.fail(f"/blog-status did not settle within {timeout}s")
    raise AssertionError("unreachable")  # for mypy


def test_generate_blog_returns_token_and_url(client: TestClient) -> None:
    resp = client.post(
        "/generate-blog", data={"report_text": "A real research report."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and body["token"]
    assert body.get("url") == f"/blog-view?token={body['token']}"


def test_generate_blog_rejects_empty_report(client: TestClient) -> None:
    resp = client.post("/generate-blog", data={"report_text": "   "})
    assert resp.status_code == 400


def test_blog_status_eventually_returns_markdown(client: TestClient) -> None:
    resp = client.post(
        "/generate-blog", data={"report_text": "A real research report."}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    body = _wait_for_ready(client, token)
    assert body["state"] == "ready", body
    assert isinstance(body.get("markdown"), str)
    assert body["markdown"]


def test_blog_status_404_for_unknown_token(client: TestClient) -> None:
    resp = client.get("/blog-status", params={"token": "definitely-not-a-real-token"})
    assert resp.status_code == 404
    assert resp.json().get("state") == "expired"


def test_blog_view_does_not_pop_cache(client: TestClient) -> None:
    """Refreshing /blog-view must keep working (no one-shot cache)."""

    resp = client.post("/generate-blog", data={"report_text": "Another report."})
    token = resp.json()["token"]
    _wait_for_ready(client, token)

    first = client.get("/blog-view", params={"token": token})
    second = client.get("/blog-view", params={"token": token})
    assert first.status_code == 200
    assert second.status_code == 200
    assert "blog-view.js" in first.text
    assert "blog-view.js" in second.text


def test_blog_view_404_for_unknown_token(client: TestClient) -> None:
    resp = client.get("/blog-view", params={"token": "nope"})
    assert resp.status_code == 404
