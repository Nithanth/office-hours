"""Tests for backwards-compatible configuration environment aliases."""

from __future__ import annotations

import pytest

import config

_CONFIG_ENV_VARS = (
    "OH_SESSION_SECRET",
    "LUX_SESSION_SECRET",
    "OH_SECRET_ENCRYPTION_KEY",
    "LUX_SECRET_ENCRYPTION_KEY",
    "OH_LOG_FORMAT_JSON",
    "LUX_LOG_FORMAT_JSON",
)


def _config_with_env(monkeypatch, **values: str) -> config.Config:
    for name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return config.Config(_env_file=None)


@pytest.mark.parametrize(
    ("new_name", "old_name", "attribute"),
    (
        ("OH_SESSION_SECRET", "LUX_SESSION_SECRET", "SESSION_SECRET"),
        (
            "OH_SECRET_ENCRYPTION_KEY",
            "LUX_SECRET_ENCRYPTION_KEY",
            "SECRET_ENCRYPTION_KEY",
        ),
    ),
)
def test_new_environment_name_takes_precedence(
    monkeypatch, new_name: str, old_name: str, attribute: str
) -> None:
    settings = _config_with_env(monkeypatch, **{new_name: "a", old_name: "b"})
    assert getattr(settings, attribute) == "a"


@pytest.mark.parametrize(
    ("new_name", "old_name", "attribute"),
    (
        ("OH_SESSION_SECRET", "LUX_SESSION_SECRET", "SESSION_SECRET"),
        (
            "OH_SECRET_ENCRYPTION_KEY",
            "LUX_SECRET_ENCRYPTION_KEY",
            "SECRET_ENCRYPTION_KEY",
        ),
    ),
)
def test_new_environment_name_is_loaded(
    monkeypatch, new_name: str, old_name: str, attribute: str
) -> None:
    settings = _config_with_env(monkeypatch, **{new_name: "a"})
    assert getattr(settings, attribute) == "a"


@pytest.mark.parametrize(
    ("new_name", "old_name", "attribute"),
    (
        ("OH_SESSION_SECRET", "LUX_SESSION_SECRET", "SESSION_SECRET"),
        (
            "OH_SECRET_ENCRYPTION_KEY",
            "LUX_SECRET_ENCRYPTION_KEY",
            "SECRET_ENCRYPTION_KEY",
        ),
    ),
)
def test_old_environment_name_remains_a_fallback(
    monkeypatch, new_name: str, old_name: str, attribute: str
) -> None:
    settings = _config_with_env(monkeypatch, **{old_name: "b"})
    assert getattr(settings, attribute) == "b"


def test_old_log_format_environment_name_remains_a_fallback(monkeypatch) -> None:
    settings = _config_with_env(monkeypatch, LUX_LOG_FORMAT_JSON="true")
    assert settings.LOG_FORMAT_JSON is True
