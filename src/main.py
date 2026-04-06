# ruff: noqa: E402,F404
"""Lux Scholar - FastAPI bootstrap.

Main responsibilities:
1. Configure global, structured logging (Loguru) and bridge stdlib logging.
2. Initialise core singletons (LLMService, ExaClient, TextExtractor) inside the
   FastAPI *lifespan* context so they live exactly as long as the app.
3. Register observability helpers (request-ID middleware + optional Prometheus
   metrics) and all API routers.
4. Mount static assets when running under `uvicorn`.
"""

import asyncio
import html
import logging
import pathlib as _pathlib
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

# ---------------------------------------------------------------------------
# Imports – *keep minimal globals above to avoid slow cold-start*
# ---------------------------------------------------------------------------
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

import config
from services.chat.chat_session import ChatSessionMiddleware
from services.chat.chat_state import _chat_states
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMConfigurationError, LLMService
from services.paper.text_extractor import TextExtractor
from utils.observability import setup_observability

# --- Optional Prometheus histogram; None when metrics are disabled ---
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover
    CHAT_TOKEN_LAT = None

# --- Tunables --------------------------------------------------------------
_IDLE_SESSION_TTL_SEC = 3600  # ChatState eviction window (seconds)
_TEXT_PREWARM_SEMA = asyncio.Semaphore(config.config.TEXT_PREWARM_CONCURRENCY)

# ---------------------------------------------------------------------------
# Background warm-cache helper (triggered by search route)
# ---------------------------------------------------------------------------


async def _prewarm_cached(url: str, paper_id: str) -> None:
    """Fetch & cache the paper HTML/PDF so later summarisation is instant."""

    from services.paper.text_extractor import TextExtractor

    async with _TEXT_PREWARM_SEMA:  # Avoid hammering remote hosts concurrently.
        try:
            await TextExtractor.instance().ensure_cached(url, paper_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prewarm_failed", paper_id=paper_id, err=str(exc))


# --- FastAPI Depends helpers ----------------------------------------------


async def get_session_id(request: Request) -> str:
    return getattr(request.state, "session_id", "anon")


def get_llm_service() -> Optional[LLMService]:
    return llm_service_instance


def get_exa_client() -> Optional[ExaClient]:
    return exa_client_instance


def get_paper_service() -> TextExtractor:
    return TextExtractor.instance()


# --- Filter noisy access logs for SSE keep-alive responses ----------------


class DropSSE204Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        msg = record.getMessage()
        return not (" 204 " in msg and "/report/stream" in msg)


logging.getLogger("uvicorn.access").addFilter(DropSSE204Filter())

# --- Bridge stdlib logging → Loguru ---------------------------------------


class _InterceptHandler(logging.Handler):
    """Forward standard 'logging' records to *loguru* for uniform formatting."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        # Retrieve corresponding Loguru level (may be numeric or name).
        try:
            level_name = logger.level(record.levelname).name
        except ValueError:
            # Fall back to numeric level converted to string for uniform type
            level_name = str(record.levelno)

        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(level_name, record.getMessage())


# Route the root logger through the intercept so third-party libs (uvicorn, fastapi, etc.)
# appear in our Loguru output.
logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

# --- Core Loguru sink ------------------------------------------------------

logger.remove()
logger.add(
    sys.stderr,
    level=config.config.LOG_LEVEL.upper(),
    serialize=config.config.LOG_FORMAT_JSON,
)

# ---------------------------------------------------------------------------
# Optional persistent log file (enable by setting LOG_FILE env variable)
# ---------------------------------------------------------------------------
log_file = config.config.LOG_FILE
if log_file:  # user opted in to file logging
    logger.add(
        log_file,
        level=config.config.LOG_LEVEL.upper(),
        serialize=config.config.LOG_FORMAT_JSON,
        rotation="20 MB",  # rotate to keep disk usage bounded
        compression="gz",  # compress rotated files
        enqueue=True,  # thread-safe with uvicorn reload workers
        backtrace=True,
    )

llm_service_instance: Optional[LLMService] = None
exa_client_instance: Optional[ExaClient] = None
# Keep GC task handle so we can cancel cleanly on shutdown
_gc_task: Optional[asyncio.Task[None]] = None

KEEP_ALIVE_SEC = config.config.CHAT_KEEPALIVE_SEC

# --- FastAPI lifespan: startup → shutdown ---------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global llm_service_instance, exa_client_instance
    logger.info("initializing services")
    try:
        if config.config.EXA_API_KEY is None:
            logger.critical("EXA_API_KEY missing; aborting startup")
            sys.exit(1)
        exa_client_instance = ExaClient(api_key=config.config.EXA_API_KEY)
        logger.info("Exa Client initialized.")

        llm_service_instance = LLMService()
        await llm_service_instance.initialize_providers()
        logger.info("LLM Service initialized.")

        async def _chatstate_gc_loop() -> None:
            while True:
                await asyncio.sleep(_IDLE_SESSION_TTL_SEC)
                now = time.time()
                removed = 0
                for sid, st in list(_chat_states.items()):
                    last_t = getattr(st, "_last_t", now)
                    if now - last_t > _IDLE_SESSION_TTL_SEC:
                        _chat_states.pop(sid, None)
                        removed += 1
                if removed:
                    logger.info("chatstate_gc", evicted=removed)

        global _gc_task
        _gc_task = asyncio.create_task(_chatstate_gc_loop())
        yield
    except ValueError as ve:
        logger.critical("startup configuration error", err=str(ve))
        sys.exit(1)
    except LLMConfigurationError as llm_config_err:
        logger.critical("llm configuration error", err=str(llm_config_err))
        sys.exit(1)
    except Exception as err:  # noqa: BLE001
        logger.exception("unhandled startup error", err=str(err))
        sys.exit(1)
    finally:
        if llm_service_instance and llm_service_instance._initialized:
            logger.info("Application shutdown: Closing LLM Service...")
            await llm_service_instance.close_all_providers()
            logger.info("LLM Service closed.")
            if _gc_task:
                _gc_task.cancel()
        else:
            logger.info("Application shutdown: LLM Service was not initialized.")


# --- FastAPI app -----------------------------------------------------------

app = FastAPI(lifespan=lifespan)

setup_observability(app)

from api.routers import (
    chat_modal as _chat_modal_router,
)
from api.routers import (
    report_notes as _report_notes_router,
)
from api.routers import (
    report_stream as _report_stream_router,
)
from api.routes import (
    admin as _admin_routes,
)
from api.routes import (
    ask_llm as _ask_llm_routes,
)
from api.routes import (
    chat as _chat_routes,
)
from api.routes import (
    metadata as _metadata_routes,
)
from api.routes import (
    pages as _pages_routes,
)
from api.routes import (
    report as _report_routes,
)
from api.routes import (
    search as _search_routes,
)

# --- Router registration ---------------------------------------------------

for _r in (
    _chat_routes.router,
    _report_routes.router,
    _search_routes.router,
    _pages_routes.router,
    _ask_llm_routes.router,
    _admin_routes.router,
    _chat_modal_router.router,
    _report_stream_router.router,
    _report_notes_router.router,
    _metadata_routes.router,
):
    app.include_router(_r)

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402

# --- Minimal security headers ---------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: D401
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "clipboard-read=*, clipboard-write=*")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # Very restrictive CSP; adjust 'img-src' and 'media-src' as needed.
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self' https: data: blob:; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com blob:; "
                "worker-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https:; "
                "frame-src 'self' https: data:; "  # allow same-origin http for viewer iframe
                "object-src https: data: blob:; "
                "frame-ancestors 'none'"
            ),
        )
        return response


app.add_middleware(ChatSessionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

import uvicorn
from slowapi.middleware import SlowAPIMiddleware

from utils.rate_limit import RateLimitExceeded, _rate_limit_exceeded_handler, limiter

# Attach rate limiter middleware & exception handling
app.add_middleware(SlowAPIMiddleware)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- Global friendly error handlers ----------------------------------------


def _html_error_banner(message: str) -> str:  # helper
    return (
        "<div class='error-banner' style='color:#d9534f;background-color:#f9f2f2;"
        "border:1px solid #d9534f;padding:10px;border-radius:4px;margin:10px 0;'>"
        f"{html.escape(message)}"
        "</div>"
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> HTMLResponse:  # noqa: D401
    """Convert HTTPException to a styled banner so HTMX swaps show feedback."""
    banner = _html_error_banner(exc.detail if exc.detail else "Request failed")
    return HTMLResponse(content=banner, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:  # noqa: D401
    """Catch-all fallback – log & return user-friendly banner."""
    logger.exception("unhandled_exception", err=str(exc))
    banner = _html_error_banner("Unexpected server error – please try again.")
    return HTMLResponse(content=banner, status_code=500)


# --- Static file mounting --------------------------------------------------

_static_candidates = [
    _pathlib.Path(__file__).parent / "static",  # current src/static
    _pathlib.Path(__file__).parent.parent / "static",  # project_root/static
]

for _static_path in _static_candidates:
    if _static_path.exists():
        logger.info("mounting static assets", path=str(_static_path))
        app.mount("/static", StaticFiles(directory=str(_static_path)), name="static")
        break
else:
    logger.warning(
        "Static directory not found in any candidate paths; static assets will return 404"
    )

# --- Manual Uvicorn entrypoint (only when python main.py) ------------------

if __name__ == "__main__":
    print(f"Starting Uvicorn server on http://{config.config.HOST}:{config.config.PORT}")
    uvicorn.run(
        "main:app",
        host=config.config.HOST,
        port=config.config.PORT,
        reload=config.config.RELOAD_APP,
        log_level="info",
    )
