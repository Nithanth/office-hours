"""Security headers middleware.

Adds strict security-related HTTP response headers to every response.
Also provides an HTTPS redirect middleware for production deployments
behind a reverse proxy.
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import RedirectResponse

import config as _cfg


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP to HTTPS in production.

    Checks ``X-Forwarded-Proto`` (set by reverse proxies like Cloudflare,
    nginx, ALB) so this works even when the app binds to plain HTTP locally.
    Only active when ``ENV`` is not ``dev`` or ``test``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if _cfg.config.ENV in ("dev", "test"):
            return await call_next(request)

        proto = (
            request.headers.get("x-forwarded-proto", "")
            or request.url.scheme
        )
        if proto != "https":
            url = request.url.replace(scheme="https")
            return RedirectResponse(str(url), status_code=301)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject common security headers into every HTTP response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains; preload",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "clipboard-read=(self), clipboard-write=(self)",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self' https: data: blob:; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://cdn.plot.ly blob:; "
                "worker-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https:; "
                "frame-src 'self' https: data:; "
                "object-src https: data: blob:; "
                "frame-ancestors 'none'"
            ),
        )
        return response
