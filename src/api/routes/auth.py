"""Google OAuth login/logout for private beta users."""

from __future__ import annotations

import secrets
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger

import config as _cfg
from services.auth.session import (
    OAUTH_STATE_COOKIE_NAME,
    clear_auth_cookie,
    clear_oauth_state_cookie,
    get_auth_user_id,
    set_auth_cookie,
    set_oauth_state_cookie,
    sign_oauth_state,
    verify_oauth_state,
)
from services.persistence.database import database_available, session_scope

router = APIRouter(tags=["auth"])

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _redirect_uri() -> str:
    return f"{_cfg.config.AUTH_REDIRECT_BASE_URL}/auth/google/callback"


def _auth_configured() -> bool:
    return bool(
        database_available()
        and _cfg.config.GOOGLE_CLIENT_ID
        and _cfg.config.GOOGLE_CLIENT_SECRET
        and _cfg.config.SECRET_ENCRYPTION_KEY
    )


@router.get("/auth/google/login")
async def google_login(
    request: Request, next: str = "/"
) -> RedirectResponse:  # noqa: A002
    if not _auth_configured():
        raise HTTPException(status_code=503, detail="Google login is not configured.")

    state = sign_oauth_state({"nonce": secrets.token_urlsafe(16), "next": next})
    params = {
        "client_id": _cfg.config.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")
    set_oauth_state_cookie(response, state, request)
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    if not _auth_configured():
        raise HTTPException(status_code=503, detail="Google login is not configured.")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state.")

    # The state token is already cryptographically signed via
    # URLSafeTimedSerializer + OH_SESSION_SECRET, so an attacker cannot
    # forge it. The cookie comparison adds defense-in-depth against CSRF by
    # binding the in-flight OAuth flow to the user's browser. On localhost
    # over plain HTTP, browsers / privacy modes sometimes drop the SameSite=Lax
    # cookie across the Google redirect, producing 400 "OAuth state mismatch"
    # on every dev sign-in. To make development frictionless without
    # weakening prod we keep strict comparison on HTTPS and downgrade to a
    # warning on HTTP (where deployments shouldn't accept real traffic
    # anyway). The signed-state verification below is always enforced.
    state_cookie = request.cookies.get(OAUTH_STATE_COOKIE_NAME)
    is_https = request.url.scheme == "https"
    if state_cookie != state:
        if state_cookie is None and not is_https:
            logger.warning(
                "oauth_state_cookie_missing_dev_fallback",
                scheme=request.url.scheme,
                host=request.url.hostname,
            )
        else:
            raise HTTPException(status_code=400, detail="OAuth state mismatch.")

    payload = verify_oauth_state(state)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "client_id": _cfg.config.GOOGLE_CLIENT_ID,
                    "client_secret": _cfg.config.GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _redirect_uri(),
                },
            )
            token_resp.raise_for_status()
            token_data: dict[str, Any] = token_resp.json()

            user_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            user_resp.raise_for_status()
            userinfo: dict[str, Any] = user_resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("google_oauth_upstream_error", status=exc.response.status_code)
        raise HTTPException(
            status_code=502, detail="Authentication failed. Please try again."
        ) from exc
    except (httpx.RequestError, KeyError) as exc:
        logger.error("google_oauth_network_error", err=str(exc))
        raise HTTPException(
            status_code=502, detail="Authentication failed. Please try again."
        ) from exc

    from services.persistence.repositories import upsert_google_user

    async for session in session_scope():
        user = await upsert_google_user(
            session,
            provider_user_id=str(userinfo["sub"]),
            email=str(userinfo["email"]),
            name=userinfo.get("name"),
            avatar_url=userinfo.get("picture"),
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
        )

    redirect_to = str(payload.get("next") or "/")
    response = RedirectResponse(redirect_to if redirect_to.startswith("/") else "/")
    clear_oauth_state_cookie(response)
    set_auth_cookie(response, user.id, request)
    return response


@router.post("/auth/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    clear_auth_cookie(response)
    return response


@router.get("/api/me")
async def me(request: Request) -> JSONResponse:
    user_id = get_auth_user_id(request)
    if not user_id or not database_available():
        return JSONResponse({"authenticated": False, "user": None})

    from services.persistence.repositories import get_user

    async for session in session_scope():
        user = await get_user(session, user_id)
    if user is None:
        return JSONResponse({"authenticated": False, "user": None})
    return JSONResponse(
        {
            "authenticated": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "avatar_url": user.avatar_url,
            },
        }
    )
