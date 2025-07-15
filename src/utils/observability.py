"""Observability helpers: request-ID middleware and optional Prometheus metrics."""

import os
import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

# Component-level logger
logger = logger.bind(component="Observability")

# ---------------------------------------------------------------------------
# Prometheus setup (optional)
# ---------------------------------------------------------------------------

_METRICS_ENABLED = bool(os.getenv("ENABLE_METRICS"))

if _METRICS_ENABLED:
    try:
        from prometheus_client import Counter, Histogram, generate_latest
    except ImportError:  # pragma: no cover – prometheus not installed
        _METRICS_ENABLED = False

if _METRICS_ENABLED:
    CHAT_REQ = Counter("chat_requests_total", "Number of /chat POST requests")
    CHAT_STREAM_TOK = Counter("chat_stream_tokens_total", "Streamed tokens sent to clients")
    CHAT_STREAM_LAT = Histogram(
        "chat_stream_latency_seconds",
        "Time to fully stream assistant response",
        buckets=(0.5, 1, 2, 5, 10, 30, 60),
    )

    # Latency between consecutive streamed tokens (UI smoothness metric)
    CHAT_TOKEN_LAT = Histogram(
        "chat_stream_token_latency_seconds",
        "Latency between streamed tokens",
        buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2),
    )

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class _RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.time()
        response: Response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        duration_ms = int((time.time() - start) * 1000)
        logger.info(
            "http_request",
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        return response


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def setup_observability(app: FastAPI) -> None:
    """Attach middleware and metrics route to *app* (idempotent)."""
    # Prevent double-registration in auto-reload dev servers
    if getattr(app.state, "_obs_setup", False):
        return
    app.state._obs_setup = True

    # Request ID + access logging middleware
    app.add_middleware(_RequestIdMiddleware)

    if _METRICS_ENABLED:

        @app.get("/metrics")
        def _metrics() -> Response:
            """Return Prometheus metrics for scraping."""
            return Response(generate_latest(), media_type="text/plain; version=0.0.4")
