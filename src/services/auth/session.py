"""Signed-cookie helpers for durable authenticated users."""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException, Request, Response, status
from itsdangerous import BadData, URLSafeTimedSerializer

import config as _cfg

AUTH_COOKIE_NAME = "oh_auth"
OAUTH_STATE_COOKIE_NAME = "oh_oauth_state"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
OAUTH_STATE_MAX_AGE = 60 * 10

_serializer = URLSafeTimedSerializer(_cfg.config.SESSION_SECRET, salt="oh-auth")


def _is_secure_cookie(request: Request) -> bool:
    """Determine if cookies should be set with Secure flag.

    Always True in production (regardless of proxy scheme detection).
    In dev, follows the actual scheme.
    """
    if _cfg.config.ENV == "prod":
        return True
    return bool(request.url.scheme == "https")


def get_auth_user_id(request: Request) -> str | None:
    """Return the signed auth user id, if present and valid."""

    cookie = request.cookies.get(AUTH_COOKIE_NAME)
    if not cookie:
        return None
    try:
        payload = _serializer.loads(cookie, max_age=AUTH_COOKIE_MAX_AGE)
    except BadData:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("user_id"), str):
        return cast(str, payload["user_id"])
    return None


def require_auth_user_id(request: Request) -> str:
    """Return current user id or raise a 401."""

    user_id = get_auth_user_id(request)
    if user_id:
        return user_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required"
    )


def set_auth_cookie(response: Response, user_id: str, request: Request) -> None:
    value = _serializer.dumps({"user_id": user_id})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        cast(str, value),
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        secure=_is_secure_cookie(request),
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")


def sign_oauth_state(payload: dict[str, Any]) -> str:
    return cast(str, _serializer.dumps(payload))


def verify_oauth_state(state: str) -> dict[str, Any]:
    try:
        payload = _serializer.loads(state, max_age=OAUTH_STATE_MAX_AGE)
    except BadData as exc:
        raise HTTPException(
            status_code=400, detail="Invalid or expired OAuth state"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    return cast(dict[str, Any], payload)


def set_oauth_state_cookie(response: Response, state: str, request: Request) -> None:
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        max_age=OAUTH_STATE_MAX_AGE,
        httponly=True,
        secure=_is_secure_cookie(request),
        samesite="lax",
        path="/",
    )


def clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(OAUTH_STATE_COOKIE_NAME, path="/")
