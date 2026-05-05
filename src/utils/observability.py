"""Observability helpers: request-ID middleware and optional Prometheus metrics."""

import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

import config as _cfg

# Component-level logger
logger = logger.bind(component="Observability")

# ---------------------------------------------------------------------------
# Prometheus setup (optional)
# ---------------------------------------------------------------------------

_METRICS_ENABLED = _cfg.config.ENABLE_METRICS

# Declare metric symbols up-front so importing modules can reference them
# whether or not prometheus_client is installed. Typed as Any so both the
# real Counter/Histogram instances below and the None fallbacks type-check.
CHAT_REQ: Any = None
CHAT_STREAM_TOK: Any = None
CHAT_STREAM_LAT: Any = None
CHAT_TOKEN_LAT: Any = None
REPORT_REQ: Any = None
REPORT_BLOCKS: Any = None
REPORT_LAT: Any = None
EXA_LAT: Any = None
LLM_TOKENS: Any = None
CACHE_HIT: Any = None

generate_latest: Any = None

if _METRICS_ENABLED:
    try:
        from prometheus_client import Counter, Histogram, generate_latest
    except ImportError:  # pragma: no cover – prometheus not installed
        _METRICS_ENABLED = False

if _METRICS_ENABLED:
    CHAT_REQ = Counter("chat_requests_total", "Number of /chat POST requests")
    CHAT_STREAM_TOK = Counter(
        "chat_stream_tokens_total", "Streamed tokens sent to clients"
    )
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

    # ---------------- Report generation metrics -----------------------------
    REPORT_REQ = Counter(
        "report_requests_total",
        "Number of /generate-report-incremental POST requests",
    )

    REPORT_BLOCKS = Counter(
        "report_blocks_total",
        "Number of summary or overview blocks streamed to clients",
    )

    REPORT_LAT = Histogram(
        "report_generation_latency_seconds",
        "Time to fully generate & stream a report",
        buckets=(1, 2, 5, 10, 30, 60, 120),
    )

    # ---------------- Additional metrics (API + caching) --------------------
    EXA_LAT = Histogram(
        "exa_request_latency_seconds",
        "Exa SDK request latency",
        buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10),
    )

    LLM_TOKENS = Counter(
        "llm_tokens_total",
        "Total tokens streamed / returned from LLM providers",
    )

    CACHE_HIT = Counter(
        "cache_hit_total",
        "Number of cache hits for LLM summary cache",
    )

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class _RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
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
