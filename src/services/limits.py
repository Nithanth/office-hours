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

from typing import Any

from fastapi import Request

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


async def check_daily_limit(request: Request, event_type: str) -> str | None:
    """Return the limit-reached message when the owner is over today's cap.

    ``None`` means the request may proceed. Fails open when the DB is
    unavailable or the event type is unknown — a counting hiccup should
    never break the product.
    """
    owner_id = prefs_owner_id(request)
    if not owner_id or not database_available():
        return None
    if owner_id in _exempt_owner_ids():
        return None

    limit_user, limit_anon, byok_exempt = _EVENT_LIMITS.get(event_type, (0, 0, False))
    if not limit_user:
        return None
    limit = limit_anon if owner_id.startswith("sess:") else limit_user

    if byok_exempt:
        try:
            ctx = await load_model_context(request)
            if ctx.openrouter_api_key:
                return None
        except Exception:  # noqa: BLE001 — fail open on prefs lookup
            pass

    from services.persistence.repositories import daily_usage_count

    try:
        async for db_session in session_scope():
            used = await daily_usage_count(
                db_session, user_id=owner_id, event_type=event_type
            )
    except Exception:  # noqa: BLE001 — fail open on counting failure
        return None
    if used >= limit:
        return limit_message(event_type)
    return None


async def record_event(request: Request, event_type: str, **metadata: Any) -> None:
    """Record a usage event for the request owner (best effort)."""
    owner_id = prefs_owner_id(request)
    if not owner_id or not database_available():
        return
    from services.persistence.repositories import record_usage

    try:
        async for db_session in session_scope():
            await record_usage(
                db_session,
                user_id=owner_id,
                event_type=event_type,
                metadata=metadata,
            )
    except Exception:  # noqa: BLE001 — never let metering break the flow
        pass


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
