"""Authentication gate middleware.

When ``REQUIRE_LOGIN`` is enabled, redirects unauthenticated users to the
Google OAuth login flow. Passes through public routes and static assets.
"""

import urllib.parse

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from config import config


# Paths that are always accessible without login.
_PUBLIC_PREFIXES = (
    "/auth/",
    "/static/",
    "/healthz",
    "/metrics",
    "/api/me",
    "/favicon.ico",
)


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Require authentication for protected routes when login is enabled."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not config.REQUIRE_LOGIN:
            return await call_next(request)

        path = request.url.path

        # Always allow public paths
        if path == "/" or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        from services.auth.session import get_auth_user_id

        if get_auth_user_id(request):
            return await call_next(request)

        # HTMX partial requests get an inline error banner
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                "<div class='error-banner'>Please sign in to continue.</div>",
                status_code=401,
            )

        # Full-page requests redirect to login
        next_url = str(request.url.path)
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(
            f"/auth/google/login?next={urllib.parse.quote(next_url)}"
        )
