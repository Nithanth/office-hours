"""Test Prometheus `/metrics` exposure gated by ENABLE_METRICS env."""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest


@pytest.mark.integration
def test_metrics_route_enabled(client, monkeypatch):
    # Force enable metrics at runtime, then re-run setup
    monkeypatch.setenv("ENABLE_METRICS", "true")

    obs_mod: ModuleType = importlib.import_module("utils.observability")

    # Toggle flag inside module and attach route again
    monkeypatch.setattr(obs_mod, "_METRICS_ENABLED", True, raising=False)
    obs_mod.setup_observability(client.app)

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


@pytest.mark.integration
def test_metrics_route_disabled(client, monkeypatch):
    # Ensure flag false
    monkeypatch.delenv("ENABLE_METRICS", raising=False)
    obs_mod: ModuleType = importlib.import_module("utils.observability")
    monkeypatch.setattr(obs_mod, "_METRICS_ENABLED", False, raising=False)

    # Recreate app with route clearing maybe but easiest: call endpoint expects 404
    resp = client.get("/metrics")
    assert resp.status_code in (404, 405)  # may differ if method not allowed
