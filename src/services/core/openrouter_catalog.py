"""TTL-cached view of the OpenRouter model catalog.

Used to validate user-entered model IDs on the settings page and to
surface price/context metadata for curated picks.
"""

from __future__ import annotations

import time

import httpx
from loguru import logger

import config as _cfg

_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_TTL_SECONDS = 600.0
_TIMEOUT_SECONDS = 10.0

# (fetched_at, {model_id: entry})
_cache: tuple[float, dict[str, dict]] | None = None


async def _fetch_catalog() -> dict[str, dict]:
    headers: dict[str, str] = {}
    api_key = _cfg.config.OPENROUTER_API_KEY
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.get(_CATALOG_URL, headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    return {m["id"]: m for m in data if isinstance(m, dict) and m.get("id")}


async def catalog() -> dict[str, dict]:
    """Return {model_id: catalog entry}, cached for ``_TTL_SECONDS``.

    On fetch failure the last good snapshot is returned; if none exists
    an empty dict is returned (validation callers treat that as
    "can't verify", not "invalid").
    """
    global _cache
    if _cache is not None and time.monotonic() - _cache[0] < _TTL_SECONDS:
        return _cache[1]
    try:
        models = await _fetch_catalog()
    except Exception as exc:  # noqa: BLE001
        logger.warning("openrouter_catalog_fetch_failed", error=str(exc))
        return _cache[1] if _cache is not None else {}
    _cache = (time.monotonic(), models)
    return models


async def model_prompt_price_per_mtok(model_id: str) -> float | None:
    """Prompt price in USD per 1M tokens, or ``None`` if unknown."""
    models = await catalog()
    entry = models.get(model_id.strip())
    if not entry:
        return None
    raw = (entry.get("pricing") or {}).get("prompt")
    if not isinstance(raw, (str, int, float)):
        return None
    try:
        return float(raw) * 1_000_000
    except (TypeError, ValueError):
        return None


async def model_exists(model_id: str) -> bool | None:
    """Whether ``model_id`` is in the OpenRouter catalog.

    Returns ``None`` when the catalog can't be fetched (caller should
    surface "couldn't verify" rather than reject).
    """
    model_id = model_id.strip()
    if not model_id:
        return False
    models = await catalog()
    if not models:
        return None
    return model_id in models
