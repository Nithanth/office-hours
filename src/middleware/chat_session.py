"""Starlette/FastAPI middleware that ensures each visitor has a unique,
HTTP-only, secure session cookie. Also exposes the session ID on the
request state for easy access throughout the request-handling pipeline.

PHASE-0 notes
-------------
* Secure=True flag is set - assumes HTTPS in production. In dev (HTTP)
  browsers will ignore the secure bit but still send the cookie back.
* The cookie is signed but not encrypted; contents are random so signing
  mainly protects against tampering.
* For multi-process deployments you should swap `itsdangerous` signing
  for a shared-secret aware solution or move to a server-side session
  store (e.g. Redis). This is left for later phases.
"""

from __future__ import annotations

import os
import secrets
from typing import cast

from fastapi import Request, Response
from itsdangerous import BadData, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

COOKIE_NAME = "luxsid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Use env var or fallback constant so dev boxes share the same secret across reloads
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
                # Tampered or invalid, ignore
                session_id = None

        if not session_id:
            session_id = secrets.token_urlsafe(16)
            cookie_to_set: str | None = cast(str, serializer.dumps(session_id))
        else:
            cookie_to_set = None  # No need to reset

        # Attach for downstream code (e.g., route handlers)
        request.state.session_id = session_id

        response = await call_next(request)

        if cookie_to_set:
            # Use secure cookies only when served over HTTPS; during local HTTP dev the
            # flag would prevent the browser from storing the cookie, which breaks
            # per-session chat state.
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
