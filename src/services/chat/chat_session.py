"""Injects a signed, HTTP-only session cookie and stores its ID on ``request.state``."""

import os
import secrets
from typing import cast

from fastapi import Request, Response
from itsdangerous import BadData, URLSafeSerializer
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

COOKIE_NAME = "luxsid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_SIGN_SECRET = os.getenv("LUX_SESSION_SECRET", "dev-insecure-secret")
serializer = URLSafeSerializer(_SIGN_SECRET, salt="lux-chat")


class ChatSessionMiddleware(BaseHTTPMiddleware):
    """Adds/validates a signed session cookie and stores session_id on request.state."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cookie_val: str | None = request.cookies.get(COOKIE_NAME)
        session_id: str | None = None
        if cookie_val:
            try:
                session_id = serializer.loads(cookie_val)
            except BadData:
                logger.warning("invalid session cookie - regenerating", cookie=cookie_val)
                session_id = None

        if not session_id:
            session_id = secrets.token_urlsafe(16)
            cookie_to_set: str | None = cast(str, serializer.dumps(session_id))
            logger.debug("new session", session_id=session_id)
        else:
            cookie_to_set = None
            logger.debug("existing session", session_id=session_id)

        request.state.session_id = session_id

        response = await call_next(request)

        if cookie_to_set:
            response.set_cookie(
                COOKIE_NAME,
                cast(str, cookie_to_set),
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
                path="/",
            )
        return response
