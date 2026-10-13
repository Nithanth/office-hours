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


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("postgres://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        ("postgresql://u:p@h:5432/db", "postgresql+asyncpg://u:p@h:5432/db"),
        (
            "postgresql+asyncpg://u:p@h:5432/db",
            "postgresql+asyncpg://u:p@h:5432/db",
        ),
        (
            "sqlite+aiosqlite:////tmp/x.db",
            "sqlite+aiosqlite:////tmp/x.db",
        ),
    ),
)
def test_database_url_scheme_is_normalized(
    monkeypatch, raw: str, expected: str
) -> None:
    settings = _config_with_env(monkeypatch, DATABASE_URL=raw)
    assert expected == settings.DATABASE_URL


def test_database_url_none_stays_none(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = _config_with_env(monkeypatch)
    assert settings.DATABASE_URL is None
