"""Centralised rate-limit helper using slowapi.

Import `limiter` and decorators from this module to apply per-route limits.
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

# Global limiter instance with sensible defaults.
# Override/extend per-route limits via the decorator.
limiter: Limiter = Limiter(
    key_func=get_remote_address, default_limits=["100/5minute"]
)  # ~0.33 r/s


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """User-friendly rate-limit response.

    - For HTMX requests: returns an HTML error banner that slots into the page.
    - For API/JSON requests: returns structured JSON with Retry-After header.
    """
    # Parse retry window from the limit string (e.g. "100 per 5 minute")
    retry_seconds = _parse_retry_seconds(str(exc.detail))

    headers: dict[str, str] = {}
    if retry_seconds:
        headers["Retry-After"] = str(retry_seconds)

    # HTMX request → return styled HTML banner
    if request.headers.get("HX-Request"):
        html = (
            "<div class='error-banner' style='"
            "padding:12px 16px;margin:8px 0;border-radius:6px;"
            "background:var(--error-bg,#fef2f2);border:1px solid var(--error-border,#fca5a5);"
            "color:var(--error-fg,#991b1b);font-size:0.9rem;"
            "'>"
            "<i class='fa-solid fa-clock' style='margin-right:6px;'></i>"
            "You're making requests too quickly. Please wait a moment and try again."
            "</div>"
        )
        return HTMLResponse(
            content=html,
            status_code=429,
            headers={
                **headers,
                "HX-Retarget": "#search-results-container",
                "HX-Reswap": "innerHTML",
            },
        )

    # JSON/API request
    return JSONResponse(
        {
            "error": "Rate limit exceeded. Please wait before retrying.",
            "retry_after": retry_seconds,
        },
        status_code=429,
        headers=headers,
    )


def _parse_retry_seconds(detail: str) -> int:
    """Best-effort parse of retry window from slowapi detail string."""
    # Example: "100 per 5 minute" → 300 seconds
    m = re.search(
        r"(\d+)\s*per\s*(\d+)?\s*(second|minute|hour|day)", detail, re.IGNORECASE
    )
    if not m:
        return 60  # Default: suggest 60s

    multiplier = int(m.group(2)) if m.group(2) else 1
    unit = m.group(3).lower()
    unit_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
    return multiplier * unit_seconds.get(unit, 60)


# Re-export helpers for convenience
__all__ = [
    "limiter",
    "RateLimitExceeded",
    "_rate_limit_exceeded_handler",
]
