"""Tests for the /healthz liveness probe endpoint."""

from __future__ import annotations


def test_healthz_returns_200(client):
    """GET /healthz should return 200."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_healthz_accessible_without_auth(client, monkeypatch):
    """Health check should be accessible even when login is required."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
