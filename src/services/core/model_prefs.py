"""Per-request model preferences: user pins and BYOK OpenRouter keys.

A :class:`ModelContext` is loaded once per request (by the global
``load_model_context`` dependency) and stored in a contextvar, so deep
call sites read the effective model ID / OpenRouter key without any
signature plumbing.

Resolution order for a tier's model ID:

1. The request owner's pin (``user_preferences.model_pins``)
2. ``OPENROUTER_MODEL_{TIER}`` env override (applied onto
   ``AVAILABLE_MODELS`` at config load)
3. The pinned ``AVAILABLE_MODELS`` default

The owner's own OpenRouter key (BYOK, stored encrypted) is consulted by
:func:`current_openrouter_api_key`; when ``None`` the shared
``OPENROUTER_API_KEY`` env is used.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import Request
from loguru import logger

import config as _cfg

_PREFS_TTL_SECONDS = 60.0


@dataclass
class ModelContext:
    """Resolved model preferences for one request owner."""

    owner_id: str | None = None
    # tier key -> OpenRouter model ID (sparse; only user-pinned tiers)
    pins: dict[str, str] = field(default_factory=dict)
    # BYOK: user's own OpenRouter key (decrypted), or None
    openrouter_api_key: str | None = None


_current: ContextVar[ModelContext | None] = ContextVar("model_context", default=None)

# owner_id -> (loaded_at, ModelContext)
_prefs_cache: dict[str, tuple[float, ModelContext]] = {}


def current_model_context() -> ModelContext:
    """Return the request's model context (default = global config)."""
    ctx = _current.get()
    return ctx if ctx is not None else ModelContext()


def set_model_context(ctx: ModelContext) -> None:
    _current.set(ctx)


def prefs_owner_id(request: Request) -> str | None:
    """Return the stable owner key for preference storage.

    Signed-in users are keyed by user id; anonymous visitors by the
    signed ``oh_sid`` session cookie. ``None`` only when neither exists
    (e.g. pre-middleware calls).
    """
    from services.auth.session import get_auth_user_id

    user_id = get_auth_user_id(request)
    if user_id:
        return str(user_id)
    session_id = getattr(request.state, "session_id", None)
    if session_id:
        return f"sess:{session_id}"
    return None


async def load_model_context(request: Request) -> ModelContext:
    """Dependency: load the request owner's prefs into the contextvar.

    Registered as an app-level dependency so it runs inside the
    endpoint's task context (required for the contextvar to propagate).
    Degrades silently to defaults when the DB is unavailable.
    """
    ctx = ModelContext()
    owner_id = prefs_owner_id(request)
    ctx.owner_id = owner_id
    if owner_id:
        cached = _prefs_cache.get(owner_id)
        if cached is not None and time.monotonic() - cached[0] < _PREFS_TTL_SECONDS:
            ctx = cached[1]
        else:
            ctx = await _load_prefs(owner_id)
            ctx.owner_id = owner_id
            _prefs_cache[owner_id] = (time.monotonic(), ctx)
    set_model_context(ctx)
    return ctx


def invalidate_prefs_cache(owner_id: str) -> None:
    """Drop a cached context after a settings write."""
    _prefs_cache.pop(owner_id, None)


async def _load_prefs(owner_id: str) -> ModelContext:
    from services.persistence import database
    from services.persistence.repositories import (
        get_user_preference,
        openrouter_api_key,
    )

    ctx = ModelContext()
    if not database.database_available():
        return ctx
    try:
        async for session in database.session_scope():
            pref = await get_user_preference(session, owner_id=owner_id)
            if pref is None:
                return ctx
            pins = pref.model_pins or {}
            ctx.pins = {k: str(v) for k, v in pins.items() if isinstance(v, str)}
            if pref.openrouter_key_encrypted:
                try:
                    ctx.openrouter_api_key = openrouter_api_key(pref)
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - bad key shouldn't break requests
                    logger.warning(
                        "model_prefs_decrypt_failed", owner_id=owner_id, error=str(exc)
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_prefs_load_failed", owner_id=owner_id, error=str(exc))
    return ctx


def resolve_model_id(model_key: str) -> str:
    """Return the effective OpenRouter model ID for a registry key.

    ``model_key`` must already be a tier key — call
    ``config.resolve_model_key`` first for user-supplied values.
    """
    cfg = _cfg.config
    ctx = current_model_context()
    pinned = ctx.pins.get(model_key)
    if pinned and pinned.strip():
        return pinned.strip()
    info = cfg.AVAILABLE_MODELS.get(model_key)
    if info:
        return str(info["id"])
    return str(cfg.AVAILABLE_MODELS[cfg.DEFAULT_MODEL_KEY]["id"])


def current_openrouter_api_key() -> str | None:
    """Return the request owner's BYOK key, or None for the shared key."""
    return current_model_context().openrouter_api_key
