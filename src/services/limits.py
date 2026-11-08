"""Daily per-owner usage limits for the beta.

One bucket per spend type:

- ``search`` — Exa calls. Always capped (our key regardless of BYOK).
- ``chat_message`` / ``report_generated`` / ``blog_generation`` /
  ``notes_generation`` — OpenRouter spend. Lifted entirely when the owner
  has a BYOK OpenRouter key (their key, their limits).
- Owners listed in ``BETA_EXEMPT_USER_IDS`` bypass every limit (demo/admin).

Limits differ for anonymous sessions (``sess:{id}``) vs signed-in users —
signing in is the free upgrade path, BYOK is the power-user path.
"""

from __future__ import annotations

import asyncio
import contextlib
import zlib
from typing import Any

from fastapi import Request
from loguru import logger

from config import config
from services.core.model_prefs import load_model_context, prefs_owner_id
from services.persistence.database import database_available, session_scope

# event_type -> (signed-in limit, anonymous-session limit, byok_exempt)
_EVENT_LIMITS: dict[str, tuple[int, int, bool]] = {
    "search": (
        config.BETA_DAILY_SEARCH_LIMIT,
        config.BETA_DAILY_SEARCH_LIMIT_ANON,
        False,
    ),
    "chat_message": (
        config.BETA_DAILY_CHAT_LIMIT,
        config.BETA_DAILY_CHAT_LIMIT_ANON,
        True,
    ),
    "report_generated": (
        config.BETA_DAILY_REPORT_LIMIT,
        config.BETA_DAILY_REPORT_LIMIT_ANON,
        True,
    ),
    "blog_generation": (
        config.BETA_DAILY_GENERATION_LIMIT,
        config.BETA_DAILY_GENERATION_LIMIT_ANON,
        True,
    ),
    "notes_generation": (
        config.BETA_DAILY_GENERATION_LIMIT,
        config.BETA_DAILY_GENERATION_LIMIT_ANON,
        True,
    ),
}

_UPGRADE_HINT = "Sign in for a higher limit, or add your own OpenRouter key in Settings to keep going."

_LIMIT_COPY: dict[str, str] = {
    "search": "You've hit today's free search limit. " + _UPGRADE_HINT,
    "chat_message": "You've hit today's message limit. Add your own OpenRouter key in Settings to keep chatting.",
    "report_generated": "You've hit today's report limit. " + _UPGRADE_HINT,
    "blog_generation": "You've hit today's generation limit. " + _UPGRADE_HINT,
    "notes_generation": "You've hit today's generation limit. " + _UPGRADE_HINT,
}

_GENERIC_LIMIT = "You've hit today's free limit. " + _UPGRADE_HINT


def _exempt_owner_ids() -> set[str]:
    raw = config.BETA_EXEMPT_USER_IDS or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def limit_message(event_type: str) -> str:
    return _LIMIT_COPY.get(event_type, _GENERIC_LIMIT)


_RESERVE_LOCK_STRIPES = 64
_reserve_locks = [asyncio.Lock() for _ in range(_RESERVE_LOCK_STRIPES)]


def _bucket_locks(*buckets: str) -> list[asyncio.Lock]:
    """In-process locks for the given buckets, in a fixed acquisition order."""
    stripes = sorted({zlib.crc32(b.encode()) % _RESERVE_LOCK_STRIPES for b in buckets})
    return [_reserve_locks[i] for i in stripes]


async def reserve_daily_quota(
    request: Request, event_type: str, *, provider: str | None = None, **metadata: Any
) -> str | None:
    """Atomically check today's cap and record one usage event.

    Returns the limit-reached message when the owner is over the cap (no
    event recorded); ``None`` means the event was recorded (or the owner is
    exempt) and the request may proceed. Anonymous owners are also capped
    per client IP, so a fresh ``oh_sid`` cookie doesn't reset the quota.
    Fails open when the DB is unavailable or the event type is unknown.
    """
    owner_id = prefs_owner_id(request)
    if not owner_id or not database_available():
        return None
    if owner_id in _exempt_owner_ids():
        return None

    limit_user, limit_anon, byok_exempt = _EVENT_LIMITS.get(event_type, (0, 0, False))
    if not limit_user:
        return None
    anonymous = owner_id.startswith("sess:")
    limit = limit_anon if anonymous else limit_user

    if byok_exempt:
        try:
            ctx = await load_model_context(request)
            if ctx.openrouter_api_key:
                return None
        except Exception:  # noqa: BLE001 — fail open on prefs lookup
            pass

    from services.persistence.repositories import reserve_usage
    from utils.rate_limit import client_ip

    ip = client_ip(request)
    ip_limit = (
        limit * max(1, config.BETA_ANON_IP_LIMIT_MULTIPLIER) if anonymous else None
    )
    buckets = [f"{event_type}:owner:{owner_id}"]
    if ip_limit is not None:
        buckets.append(f"{event_type}:ip:{ip}")

    try:
        async with contextlib.AsyncExitStack() as stack:
            for lock in _bucket_locks(*buckets):
                await stack.enter_async_context(lock)
            allowed = True
            async for db_session in session_scope():
                allowed = await reserve_usage(
                    db_session,
                    user_id=owner_id,
                    event_type=event_type,
                    limit=limit,
                    client_ip=ip,
                    ip_limit=ip_limit,
                    provider=provider,
                    metadata=metadata,
                )
    except Exception as exc:  # noqa: BLE001 — fail open on counting failure
        logger.warning(
            "daily_quota_reserve_failed", event_type=event_type, error=str(exc)
        )
        return None
    if not allowed:
        return limit_message(event_type)
    return None


def friendly_error_message(exc: BaseException | str, fallback: str) -> str:
    """Map provider/API failures to human copy.

    Raw provider errors (401, insufficient_quota, rate-limit bodies) must
    never reach the page — they read as esoteric failures and leak service
    details. Log the real error at the call site; show this instead.
    """
    text = str(exc).lower()
    if any(
        tok in text
        for tok in (
            "insufficient_quota",
            "insufficient balance",
            "401",
            "unauthorized",
            "invalid api key",
            "authentication",
        )
    ):
        return (
            "Our AI backend is having an issue right now. Try again in a few minutes."
        )
    if any(
        tok in text for tok in ("429", "rate limit", "rate_limit", "too many requests")
    ):
        return "We're a bit busy right now. Try again in a minute."
    if any(
        tok in text for tok in ("timeout", "timed out", "connection", "network", "dns")
    ):
        return "Couldn't reach our backend. Check your connection and try again."
    return fallback
