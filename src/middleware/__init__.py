"""Application middleware registration.

Provides a single ``register_all(app)`` entry point that attaches all
middleware in the correct order (outermost first in add_middleware means
it executes last in the request lifecycle).
"""

from fastapi import FastAPI

from middleware.auth_gate import AuthGateMiddleware
from middleware.security import SecurityHeadersMiddleware
from services.chat.chat_session import ChatSessionMiddleware
from utils.rate_limit import RateLimitExceeded, _rate_limit_exceeded_handler, limiter

from slowapi.middleware import SlowAPIMiddleware


def register_all(app: FastAPI) -> None:
    """Attach all middleware layers to *app* in the correct order.

    Starlette/FastAPI processes middleware in reverse registration order
    (last added = outermost = first to run). We register from outermost
    to innermost:
      1. ChatSession (populates session state)
      2. AuthGate (checks login)
      3. SecurityHeaders (adds response headers)
      4. SlowAPI rate limiter
    """
    app.add_middleware(ChatSessionMiddleware)
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SlowAPIMiddleware)

    # Attach rate limiter state & exception handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
