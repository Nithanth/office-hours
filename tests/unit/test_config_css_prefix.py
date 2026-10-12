"""Tests for environment-driven CSS prefix selection in `config.py`."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("env", "css_prefix_env", "expected"),
    [
        ("dev", None, "css"),
        ("prod", None, "css/dist"),
        ("prod", "custom/path", "custom/path"),
        ("stage", "alt", "alt"),
    ],
)
def test_css_prefix(monkeypatch, env: str, css_prefix_env: str | None, expected: str):
    """`config.CSS_PREFIX` should follow override > ENV heuristic."""

    # Reset module state between parameter sets
    monkeypatch.delenv("CSS_PREFIX", raising=False)
    monkeypatch.setenv("ENV", env)
    # Non-dev/test envs require a non-default session secret to avoid sys.exit(1)
    if env not in ("dev", "test"):
        monkeypatch.setenv("OH_SESSION_SECRET", "test-secret-for-css-prefix")
    if css_prefix_env is not None:
        monkeypatch.setenv("CSS_PREFIX", css_prefix_env)

    # Re-import to pick up env change
    import config

    importlib.reload(config)
    assert expected == config.config.CSS_PREFIX
