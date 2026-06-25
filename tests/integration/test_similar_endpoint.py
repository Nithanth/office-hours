"""Integration test for the /api/similar endpoint."""

from __future__ import annotations

from starlette.testclient import TestClient


def test_similar_endpoint_requires_url(client: TestClient) -> None:
    """POST /api/similar without paper_url should return 422 (validation error)."""
    resp = client.post("/api/similar")
    assert resp.status_code == 422  # FastAPI validation error


def test_similar_endpoint_returns_html(client: TestClient) -> None:
    """POST /api/similar with a URL returns an HTML fragment."""
    resp = client.post(
        "/api/similar",
        data={
            "paper_url": "https://arxiv.org/abs/2106.09685",
            "paper_title": "LoRA",
        },
    )
    assert resp.status_code == 200
    # Should return HTML (not JSON)
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    # Should contain either recommendations or an error message in a panel
    assert "similar-panel" in body or "similar-container" in body or "checkbox" in body
