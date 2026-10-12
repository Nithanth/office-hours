"""Tests for per-request model preferences (pins, BYOK, resolution)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import config
from services.core import model_prefs
from services.core.model_prefs import ModelContext
from services.persistence.database import Base
from services.persistence.repositories import (
    delete_openrouter_key,
    get_user_preference,
    openrouter_api_key,
    set_openrouter_key,
    upsert_model_pins,
)

# -- resolve_model_id ------------------------------------------------------


def test_resolve_model_id_returns_pinned_default() -> None:
    cfg = config.Config(_env_file=None)
    model_prefs.set_model_context(ModelContext())
    assert model_prefs.resolve_model_id("deep") == cfg.AVAILABLE_MODELS["deep"]["id"]


def test_resolve_model_id_prefers_user_pin() -> None:
    model_prefs.set_model_context(ModelContext(pins={"deep": "openai/gpt-5.5-pro"}))
    try:
        assert model_prefs.resolve_model_id("deep") == "openai/gpt-5.5-pro"
        # Unpinned tiers still fall back to the registry.
        assert model_prefs.resolve_model_id("fast") == "google/gemini-3.5-flash"
    finally:
        model_prefs.set_model_context(ModelContext())


def test_resolve_model_id_falls_back_to_default_tier() -> None:
    cfg = config.Config(_env_file=None)
    model_prefs.set_model_context(ModelContext())
    assert (
        model_prefs.resolve_model_id("nonexistent-tier")
        == cfg.AVAILABLE_MODELS[cfg.DEFAULT_MODEL_KEY]["id"]
    )


# -- owner resolution -------------------------------------------------------


def _request(session_id: str | None = None, cookies: dict | None = None) -> object:
    return SimpleNamespace(
        state=SimpleNamespace(session_id=session_id),
        cookies=cookies or {},
    )


def test_prefs_owner_id_uses_session_for_anon() -> None:
    owner = model_prefs.prefs_owner_id(_request(session_id="abc123"))  # type: ignore[arg-type]
    assert owner == "sess:abc123"


def test_prefs_owner_id_none_without_session() -> None:
    assert model_prefs.prefs_owner_id(_request()) is None  # type: ignore[arg-type]


# -- BYOK accessor -----------------------------------------------------------


def test_current_openrouter_api_key_reads_context() -> None:
    model_prefs.set_model_context(ModelContext(openrouter_api_key="sk-or-test"))
    try:
        assert model_prefs.current_openrouter_api_key() == "sk-or-test"
    finally:
        model_prefs.set_model_context(ModelContext())
    assert model_prefs.current_openrouter_api_key() is None


# -- TTL cache ---------------------------------------------------------------


def test_prefs_cache_invalidation() -> None:
    model_prefs._prefs_cache["x"] = (time.monotonic(), ModelContext(pins={"fast": "m"}))
    model_prefs.invalidate_prefs_cache("x")
    assert "x" not in model_prefs._prefs_cache


# -- repository round-trip (SQLite) ------------------------------------------


@pytest.mark.asyncio
async def test_preference_repo_round_trip(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from services.auth import crypto

    monkeypatch.setattr(
        config.config, "SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    crypto._fernet.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        pref = await upsert_model_pins(
            session, owner_id="sess:t1", pins={"deep": "openai/gpt-5.5"}
        )
        assert pref.model_pins == {"deep": "openai/gpt-5.5"}

        await set_openrouter_key(session, owner_id="sess:t1", api_key="sk-or-abc")
        pref = await get_user_preference(session, owner_id="sess:t1")
        assert pref is not None
        assert pref.openrouter_key_encrypted != "sk-or-abc"  # encrypted at rest
        assert openrouter_api_key(pref) == "sk-or-abc"  # round-trips

        await upsert_model_pins(session, owner_id="sess:t1", pins={"fast": "m2"})
        pref = await get_user_preference(session, owner_id="sess:t1")
        assert pref is not None and pref.model_pins == {"fast": "m2"}

        await delete_openrouter_key(session, owner_id="sess:t1")
        pref = await get_user_preference(session, owner_id="sess:t1")
        assert pref is not None and pref.openrouter_key_encrypted is None

    await engine.dispose()


# -- catalog validation --------------------------------------------------------


@pytest.mark.asyncio
async def test_model_exists_against_cached_catalog(monkeypatch) -> None:
    from services.core import openrouter_catalog

    async def fake_fetch() -> dict[str, dict]:
        return {"google/gemini-3.5-flash": {"id": "google/gemini-3.5-flash"}}

    monkeypatch.setattr(openrouter_catalog, "_fetch_catalog", fake_fetch)
    openrouter_catalog._cache = None
    assert await openrouter_catalog.model_exists("google/gemini-3.5-flash") is True
    assert await openrouter_catalog.model_exists("bogus/model") is False


@pytest.mark.asyncio
async def test_model_exists_unknown_when_catalog_unavailable(monkeypatch) -> None:
    from services.core import openrouter_catalog

    monkeypatch.setattr(
        openrouter_catalog,
        "_fetch_catalog",
        AsyncMock(side_effect=RuntimeError("down")),
    )
    openrouter_catalog._cache = None
    assert await openrouter_catalog.model_exists("anything/here") is None
