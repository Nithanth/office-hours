"""Tests for the OH_* configuration environment variable names."""

from __future__ import annotations

import pytest

import config

_CONFIG_ENV_VARS = (
    "OH_SESSION_SECRET",
    "OH_SECRET_ENCRYPTION_KEY",
    "OH_LOG_FORMAT_JSON",
)


def _config_with_env(monkeypatch, **values: str) -> config.Config:
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return config.Config(_env_file=None)


@pytest.mark.parametrize(
    ("env_name", "attribute"),
    (
        ("OH_SESSION_SECRET", "SESSION_SECRET"),
        ("OH_SECRET_ENCRYPTION_KEY", "SECRET_ENCRYPTION_KEY"),
    ),
)
def test_environment_name_is_loaded(monkeypatch, env_name: str, attribute: str) -> None:
    settings = _config_with_env(monkeypatch, **{env_name: "a"})
    assert getattr(settings, attribute) == "a"


def test_log_format_environment_name_is_loaded(monkeypatch) -> None:
    settings = _config_with_env(monkeypatch, OH_LOG_FORMAT_JSON="true")
    assert settings.LOG_FORMAT_JSON is True
