"""Application middleware registration.

Provides a single ``register_all(app)`` entry point that attaches all
middleware in the correct order (outermost first in add_middleware means
it executes last in the request lifecycle).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware

import config as _cfg
from middleware.auth_gate import AuthGateMiddleware
from middleware.security import HTTPSRedirectMiddleware, SecurityHeadersMiddleware
from services.chat.chat_session import ChatSessionMiddleware
from utils.rate_limit import RateLimitExceeded, _rate_limit_exceeded_handler, limiter


def register_all(app: FastAPI) -> None:
    """Attach all middleware layers to *app* in the correct order.

    Starlette/FastAPI processes middleware in reverse registration order
    (last added = outermost = first to run). We register from outermost
    to innermost:
      1. HTTPSRedirect (prod only — redirect HTTP→HTTPS)
      2. ChatSession (populates session state)
      3. AuthGate (checks login)
      4. SecurityHeaders (adds response headers)
      5. CORS (explicit origin allowlist)
      6. SlowAPI rate limiter
    """
    app.add_middleware(HTTPSRedirectMiddleware)
    app.add_middleware(ChatSessionMiddleware)
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS — restrict to same-origin in production; allow localhost in dev.
    allowed_origins: list[str] = []
    base_url = _cfg.config.AUTH_REDIRECT_BASE_URL
    if base_url:
        allowed_origins.append(base_url.rstrip("/"))
    if _cfg.config.ENV == "dev":
        allowed_origins.extend(
            [
                "http://localhost:8000",
                "http://127.0.0.1:8000",
            ]
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.add_middleware(SlowAPIMiddleware)

    # Attach rate limiter state & exception handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
