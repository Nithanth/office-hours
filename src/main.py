# ruff: noqa: E402,F404
"""Lux Scholar - FastAPI bootstrap.

Main responsibilities:
1. Configure structured logging (delegated to middleware.logging_setup).
2. Initialise core singletons (LLMService, ExaClient, TextExtractor) inside the
   FastAPI *lifespan* context so they live exactly as long as the app.
3. Register middleware (delegated to middleware package) and all API routers.
4. Mount static assets when running under ``uvicorn``.
"""

import asyncio
import html
import os
import pathlib as _pathlib
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

# Silence the lance Rust crate's tracing output before any imports.
os.environ.setdefault("RUST_LOG", "lance=error")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

import config
from middleware.logging_setup import configure_logging
from services.chat.chat_state import _chat_states
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMConfigurationError, LLMService
from services.paper.text_extractor import TextExtractor
from services.persistence.database import close_database, init_database
from services.tasks import task_queue
from utils.observability import setup_observability

# --- Configure logging early ------------------------------------------------
configure_logging()

# --- Optional Prometheus histogram; None when metrics are disabled ---
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover
    CHAT_TOKEN_LAT = None

# --- Tunables --------------------------------------------------------------
_IDLE_SESSION_TTL_SEC = 3600  # ChatState eviction window (seconds)

# Configure task queue concurrency for prewarming
task_queue.set_concurrency("prewarm", config.config.TEXT_PREWARM_CONCURRENCY)

# ---------------------------------------------------------------------------
# Background warm-cache helper (triggered by search route)
# ---------------------------------------------------------------------------


async def _prewarm_cached(url: str, paper_id: str) -> None:
    """Fetch & cache the paper HTML/PDF so later summarisation is instant."""
    from services.paper.text_extractor import TextExtractor

    await TextExtractor.instance().ensure_cached(url, paper_id)


# --- FastAPI Depends helpers ------------------------------------------------

llm_service_instance: Optional[LLMService] = None
exa_client_instance: Optional[ExaClient] = None
_gc_task: Optional[asyncio.Task[None]] = None

KEEP_ALIVE_SEC = config.config.CHAT_KEEPALIVE_SEC


async def get_session_id(request: Request) -> str:
    return getattr(request.state, "session_id", "anon")


def get_llm_service() -> Optional[LLMService]:
    return llm_service_instance


def get_exa_client() -> Optional[ExaClient]:
    return exa_client_instance


def get_paper_service() -> TextExtractor:
    return TextExtractor.instance()


# ---------------------------------------------------------------------------
# FastAPI lifespan: startup → shutdown
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global llm_service_instance, exa_client_instance
    logger.info("initializing services")
    try:
        config.config.validate_model_registry()
        await init_database()
        if config.config.EXA_API_KEY is None:
            logger.critical("EXA_API_KEY missing; aborting startup")
            sys.exit(1)
        exa_client_instance = ExaClient(api_key=config.config.EXA_API_KEY)
        logger.info("Exa Client initialized.")

        llm_service_instance = LLMService()
        await llm_service_instance.initialize_providers()
        logger.info("LLM Service initialized.")

        async def _chatstate_gc_loop() -> None:
            from services.chat.stores import get_store

            while True:
                await asyncio.sleep(_IDLE_SESSION_TTL_SEC)
                store = get_store()
                now = time.time()
                removed = 0
                for sid, st in list(_chat_states.items()):
                    last_t = getattr(st, "_last_t", now)
                    if now - last_t > _IDLE_SESSION_TTL_SEC:
                        _chat_states.pop(sid, None)
                        try:
                            store.delete_session(sid)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "gc_store_delete_failed", session_id=sid, err=str(exc)
                            )
                        removed += 1
                if removed:
                    logger.info("chatstate_gc", evicted=removed)
                # Also clean up old task queue records
                purged = task_queue.clear_completed(
                    older_than_sec=_IDLE_SESSION_TTL_SEC
                )
                if purged:
                    logger.debug("taskqueue_gc", purged=purged)

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
        await close_database()


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan)

# Observability (request-ID middleware + /metrics endpoint)
setup_observability(app)

# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

from api.routers import chat_modal as _chat_modal_router
from api.routers import report_notes as _report_notes_router
from api.routers import report_stream as _report_stream_router
from api.routes import admin as _admin_routes
from api.routes import ask_llm as _ask_llm_routes
from api.routes import auth as _auth_routes
from api.routes import author as _author_routes
from api.routes import chat as _chat_routes
from api.routes import cluster as _cluster_routes
from api.routes import history as _history_routes
from api.routes import integrations as _integration_routes
from api.routes import metadata as _metadata_routes
from api.routes import pages as _pages_routes
from api.routes import report as _report_routes
from api.routes import search as _search_routes
from api.routes import similar as _similar_routes

for _r in (
    _chat_routes.router,
    _cluster_routes.router,
    _report_routes.router,
    _search_routes.router,
    _auth_routes.router,
    _history_routes.router,
    _integration_routes.router,
    _pages_routes.router,
    _ask_llm_routes.router,
    _admin_routes.router,
    _similar_routes.router,
    _author_routes.router,
    _chat_modal_router.router,
    _report_stream_router.router,
    _report_notes_router.router,
    _metadata_routes.router,
):
    app.include_router(_r)

# ---------------------------------------------------------------------------
# Middleware (security, auth, rate limiting, sessions)
# ---------------------------------------------------------------------------

from middleware import register_all as _register_middleware

_register_middleware(app)

# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------


def _html_error_banner(message: str) -> str:
    return (
        "<div class='error-banner' style='color:#d9534f;background-color:#f9f2f2;"
        "border:1px solid #d9534f;padding:10px;border-radius:4px;margin:10px 0;'>"
        f"{html.escape(message)}"
        "</div>"
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> HTMLResponse:
    """Convert HTTPException to a styled banner so HTMX swaps show feedback."""
    banner = _html_error_banner(exc.detail if exc.detail else "Request failed")
    return HTMLResponse(content=banner, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> HTMLResponse:
    """Catch-all fallback – log & return user-friendly banner."""
    logger.exception("unhandled_exception", err=str(exc))
    banner = _html_error_banner("Unexpected server error – please try again.")
    return HTMLResponse(content=banner, status_code=500)


# ---------------------------------------------------------------------------
# Static file mounting
# ---------------------------------------------------------------------------

_static_candidates = [
    _pathlib.Path(__file__).parent / "static",
    _pathlib.Path(__file__).parent.parent / "static",
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

# ---------------------------------------------------------------------------
# Manual Uvicorn entrypoint (only when python main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    print(
        f"Starting Uvicorn server on http://{config.config.HOST}:{config.config.PORT}"
    )
    uvicorn.run(
        "main:app",
        host=config.config.HOST,
        port=config.config.PORT,
        reload=config.config.RELOAD_APP,
        log_level="info",
    )
