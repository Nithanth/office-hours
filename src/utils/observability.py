"""Lightweight observability helpers (logging + Prometheus) that degrade gracefully.

Usage:
    from utils.observability import setup_observability
    setup_observability(app)

This injects:
  • structlog configured JSON logger (falls back to stdlib logging).
  • request-id middleware (X-Request-Id header).
  • Prometheus counters/histograms if ``prometheus_client`` is installed and
    ``ENABLE_METRICS`` env var is truthy. Metrics available at ``/metrics``.

The helpers are intentionally dependency-tolerant: if optional packages are not
present the application still runs.
"""

from __future__ import annotations

import logging
import os
import time
import types
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# structlog setup (optional)
# ---------------------------------------------------------------------------

try:
    import structlog
except ImportError:  # pragma: no cover – structlog not installed

    class _DummyLogger:
        def __getattr__(self, name: str) -> Callable[..., None]:  # noqa: D401
            def _no_op(*_args: Any, **_kwargs: Any) -> None:  # noqa: D401
                return None

            return _no_op

    structlog = types.ModuleType("structlog_dummy")

    def get_logger() -> _DummyLogger:
        return _DummyLogger()

    structlog.get_logger = get_logger

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
        duration = time.time() - start
        structlog.get_logger().info(
            "http_request",
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=int(duration * 1000),
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

    # Configure structlog to JSON on stdout if not already configured
    if not structlog.is_configured():
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        )

    app.add_middleware(_RequestIdMiddleware)

    if _METRICS_ENABLED:

        @app.get("/metrics")
        def _metrics() -> Response:  # noqa: D401
            return Response(generate_latest(), media_type="text/plain; version=0.0.4")
