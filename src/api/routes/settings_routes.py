"""Settings routes: model-tier pinning and BYOK OpenRouter key management."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger

import config as _cfg
from pages.settings_page import settings_page
from services.core import model_prefs, openrouter_catalog
from services.persistence import database
from services.persistence.repositories import (
    delete_openrouter_key,
    set_openrouter_key,
    upsert_model_pins,
)

router = APIRouter()

_TIERS = ("fast", "standard", "deep")


def _render(
    request: Request, *, saved: bool = False, error: str | None = None
) -> HTMLResponse:
    ctx = model_prefs.current_model_context()
    return HTMLResponse(
        f"<!DOCTYPE html>{settings_page(pins=ctx.pins, openrouter_key=ctx.openrouter_api_key, saved=saved, error=error)}"
    )


@router.get("/settings", response_class=HTMLResponse)
async def get_settings_page(request: Request, saved: str | None = None) -> HTMLResponse:
    return _render(request, saved=bool(saved))


@router.post("/settings/models")
async def save_model_prefs(request: Request) -> Response:
    ctx = model_prefs.current_model_context()
    owner_id = ctx.owner_id
    if not owner_id:
        return _render(
            request,
            error="Could not determine your session — please reload and try again.",
        )

    form = await request.form()
    pins: dict[str, str] = {}
    invalid: list[str] = []
    for tier in _TIERS:
        raw = str(form.get(f"pin_{tier}") or "").strip()
        value = ""
        if raw == "__custom__":
            value = str(form.get(f"custom_{tier}") or "").strip()
        elif raw:
            value = raw
        if not value:
            continue  # tier stays on the default pin
        exists = await openrouter_catalog.model_exists(value)
        if exists is False:
            invalid.append(f"{tier}: '{value}' is not a known OpenRouter model ID")
            continue
        tier_error = await _tier_fit_error(tier, value)
        if tier_error:
            invalid.append(tier_error)
        else:
            pins[tier] = value

    if invalid:
        return _render(request, error="; ".join(invalid))

    if database.database_available():
        try:
            async for session in database.session_scope():
                await upsert_model_pins(session, owner_id=owner_id, pins=pins)
        except Exception as exc:  # noqa: BLE001
            logger.error("settings_save_pins_failed", error=str(exc))
            return _render(
                request, error="Couldn't save preferences — please try again."
            )
        model_prefs.invalidate_prefs_cache(owner_id)
    else:
        # No DB configured: keep pins in the request-context cache so they
        # still apply for this session's lifetime.
        _prefs_direct_set(owner_id, pins)

    return RedirectResponse("/settings?saved=1", status_code=303)


async def _tier_fit_error(tier: str, model_id: str) -> str | None:
    """Reject pins whose model doesn't fit the tier's purpose.

    Curated models are checked against their declared ``tiers``; custom
    IDs are checked against the OpenRouter prompt-price band for the
    tier (``TIER_PRICE_BANDS_USD_PER_MTOK``). Unknown price → allow,
    since we can't verify.
    """
    for m in _cfg.config.CURATED_OPENROUTER_MODELS:
        if m["id"] == model_id:
            if tier not in m.get("tiers", []):
                return (
                    f"{tier}: '{model_id}' doesn't fit the "
                    f"{tier.capitalize()} tier (pick a {tier}-class model)"
                )
            return None

    band = _cfg.config.TIER_PRICE_BANDS_USD_PER_MTOK.get(tier)
    if band is None:
        return None
    price = await openrouter_catalog.model_prompt_price_per_mtok(model_id)
    if price is None:
        return None  # can't verify — allow
    lo, hi = band
    if not (lo <= price <= hi):
        return (
            f"{tier}: '{model_id}' costs ${price:.2f}/MTok — outside the "
            f"{tier.capitalize()} tier's range"
        )
    return None


def _prefs_direct_set(owner_id: str, pins: dict[str, str]) -> None:
    """Fallback when no DB is configured: update the TTL cache directly."""
    ctx = model_prefs.ModelContext(owner_id=owner_id, pins=pins)
    import time

    model_prefs._prefs_cache[owner_id] = (time.monotonic(), ctx)


@router.post("/settings/openrouter-key")
async def save_openrouter_key(
    request: Request, openrouter_key: str = Form("")  # noqa: B008
) -> Response:
    ctx = model_prefs.current_model_context()
    owner_id = ctx.owner_id
    key = openrouter_key.strip()
    if not key:
        return _render(request, error="Enter an OpenRouter API key.")
    if not key.startswith("sk-or-"):
        return _render(
            request,
            error="That doesn't look like an OpenRouter key (expected sk-or-…).",
        )
    if not owner_id:
        return _render(
            request,
            error="Could not determine your session — please reload and try again.",
        )
    if not database.database_available():
        return _render(
            request,
            error="Saved keys require a database; set OPENROUTER_API_KEY in the environment instead.",
        )

    try:
        async for session in database.session_scope():
            await set_openrouter_key(session, owner_id=owner_id, api_key=key)
    except Exception as exc:  # noqa: BLE001
        logger.error("settings_save_key_failed", error=str(exc))
        return _render(
            request,
            error="Couldn't save the key (is OH_SECRET_ENCRYPTION_KEY configured?).",
        )
    model_prefs.invalidate_prefs_cache(owner_id)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/openrouter-key/delete")
async def remove_openrouter_key(request: Request) -> RedirectResponse:
    ctx = model_prefs.current_model_context()
    if ctx.owner_id and database.database_available():
        try:
            async for session in database.session_scope():
                await delete_openrouter_key(session, owner_id=ctx.owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("settings_delete_key_failed", error=str(exc))
        model_prefs.invalidate_prefs_cache(ctx.owner_id)
    return RedirectResponse("/settings?saved=1", status_code=303)
