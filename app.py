# ruff: noqa: E402,F404
"""Shim module - retains legacy import path `import app`.

This file now simply re-exports `lux_scholar.main` so that any
third-party code or tests that still perform `import app` will continue
working during the migration.
"""

import sys as _sys
from importlib import import_module as _imp

_mod = _imp("main")
# Register under the legacy name
_sys.modules[__name__] = _mod

# Also copy exported symbols into current module scope for lint / help()
globals().update(_mod.__dict__)

# The remaining legacy code below is kept for backward compatibility.
# Ensure required standard-library imports exist so that it can execute
# without NameError even though the real application logic now lives
# in `lux_scholar.main`.
import asyncio
import logging
import os
import pathlib as _pathlib
import sys as _sys
from importlib import import_module as _imp

_mod = _imp("main")
_sys.modules[__name__] = _mod

import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

import config
from chat_state import _chat_states
from middleware.chat_session import ChatSessionMiddleware
from services.exa_client import ExaClient
from services.llm_client import LLMConfigurationError, LLMService
from services.text_extractor import TextExtractor
from utils.observability import setup_observability

# Histogram only present when metrics enabled; fall back to None when absent
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover
    CHAT_TOKEN_LAT = None


# --- Helper Accessors --------------------------------------------------

_IDLE_SESSION_TTL_SEC = 3600  # 1 h

# Limit concurrent TextExtractor pre-warms to avoid stampede
_TEXT_PREWARM_SEMA = asyncio.Semaphore(int(os.getenv("TEXT_PREWARM_CONCURRENCY", "8")))


async def _prewarm_cached(url: str, paper_id: str) -> None:
    from services.text_extractor import TextExtractor

    async with _TEXT_PREWARM_SEMA:
        try:
            await TextExtractor.instance().ensure_cached(url, paper_id)
        except Exception as exc:
            logger.warning("prewarm_failed", paper_id=paper_id, err=str(exc))


async def get_session_id(request: Request) -> str:  # FastAPI Depends helper
    """Extracts the visitor's session_id set by ChatSessionMiddleware."""
    return getattr(request.state, "session_id", "anon")


def get_llm_service() -> Optional[LLMService]:
    """Return the singleton LLMService instance if initialized."""
    return llm_service_instance


def get_exa_client() -> Optional[ExaClient]:
    """Return the singleton ExaClient instance if initialized."""
    return exa_client_instance


def get_paper_service() -> TextExtractor:
    """For now, reuse TextExtractor as a minimal paper-content service."""
    return TextExtractor.instance()


class DropSSE204Filter(logging.Filter):
    """Filter out boring 204 responses for /report/stream."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 – signature required by logging
        msg = record.getMessage()
        # Keep everything *except* 204 responses for the incremental report SSE.
        return not (" 204 " in msg and "/report/stream" in msg)


logging.getLogger("uvicorn.access").addFilter(DropSSE204Filter())

# ------------------- Loguru (application) logger ----------------
logger.remove()  # Remove default handler
logger.add(
    sys.stderr,
    level=config.config.LOG_LEVEL.upper(),  # Ensure level is uppercase for loguru
    serialize=config.config.LOG_FORMAT_JSON,  # Enable JSON logging based on config
)

# Global LLM Service instance
llm_service_instance: Optional[LLMService] = None

# Global Exa Client instance
exa_client_instance: Optional[ExaClient] = None

# --- Global State for Active Tasks ---

KEEP_ALIVE_SEC = int(os.getenv("CHAT_KEEPALIVE_SEC", "1"))

# --- Main Application Setup ------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle hook to initialize services.

    Initializes the LLM Service and Exa Client instances.

    Args:
        app: The FastAPI application instance.

    Yields:
        None
    """
    global llm_service_instance
    global exa_client_instance
    logger.info("Application startup: Initializing LLM Service and Exa Client...")

    try:
        if config.config.EXA_API_KEY is None:
            logger.critical("EXA_API_KEY is not set. Application cannot start.")
            sys.exit(1)
        exa_client_instance = ExaClient(api_key=config.config.EXA_API_KEY)
        logger.info("Exa Client initialized.")

        llm_service_instance = LLMService()
        await llm_service_instance.initialize_providers()
        logger.info("LLM Service initialized.")

        # Launch background ChatState GC loop
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

        gc_task = asyncio.create_task(_chatstate_gc_loop())
        yield
    except ValueError as ve:
        logger.critical(f"Configuration error during startup: {ve}. Application cannot start.")
        sys.exit(1)
    except LLMConfigurationError as llm_config_err:
        logger.critical(
            f"LLM Configuration error during startup: {llm_config_err}. Application cannot start."
        )
        sys.exit(1)
    except Exception as e:
        logger.exception(
            f"Critical error during service initialization: {e}. Application cannot start."
        )
        # Optionally, re-raise or handle more gracefully depending on deployment strategy
        sys.exit(1)  # Ensure application exits on any other unexpected error during critical init
    finally:
        if llm_service_instance and llm_service_instance._initialized:
            logger.info("Application shutdown: Closing LLM Service...")
            await llm_service_instance.close_all_providers()
            logger.info("LLM Service closed.")
            if "gc_task" in locals():
                gc_task.cancel()
        else:
            logger.info("Application shutdown: LLM Service was not initialized.")


app = FastAPI(lifespan=lifespan)

# Observability setup
setup_observability(app)

# Import routers lazily to avoid circular import during module initialization.
try:
    from importlib import import_module

    _report_stream = import_module("routers.report_stream")
    _chat_modal = import_module("routers.chat_modal")

    app.include_router(_report_stream.router)
    app.include_router(_chat_modal.router)
except Exception as _imp_err:  # noqa: BLE001 – log and continue startup
    logger.warning("Could not import routers: %s", _imp_err)

# Attach per-visitor session cookie middleware (Secure=True flag set)
app.add_middleware(ChatSessionMiddleware)


# Security headers / permissions-policy
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set common security headers on every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: D401 – simple middleware
        response: Response = await call_next(request)

        # Enforce HTTPS + preload (has no effect on localhost)
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"
        )
        # Disable MIME type sniffing
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Limit referrer leakage
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # Allow rich clipboard copy without permission prompt (Safari)
        response.headers.setdefault("Permissions-Policy", "clipboard-read=*, clipboard-write=*")

        return response


app.add_middleware(SecurityHeadersMiddleware)

# --------------------------------------------------
# Static Files Mount (CSS, JS, images)
# --------------------------------------------------

_static_path = _pathlib.Path(__file__).parent / "static"
if _static_path.exists():
    app.mount("/static", StaticFiles(directory=str(_static_path)), name="static")
else:
    logger.warning("Static directory not found at %s; static assets will return 404", _static_path)

# All endpoint handlers live in `api/routes/*`; `app.py` remains a thin bootstrap.

# --- Main Execution Block --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(f"Starting Uvicorn server on http://{config.config.HOST}:{config.config.PORT}")
    uvicorn.run(
        "app:app",
        host=config.config.HOST,
        port=config.config.PORT,
        reload=config.config.RELOAD_APP,
        log_level="info",
    )
