# ruff: noqa: E402,F404
"""Lux Scholar FastAPI application bootstrap (new canonical path).

This file was moved from the project-root `app.py` during the
re-organisation.  The legacy `app.py` now simply re-exports
`lux_scholar.main` so that existing imports continue to function.
"""

import asyncio
import logging
import os
import pathlib as _pathlib
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

# CHAT_TOKEN_LAT is defined only when metrics enabled
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover
    CHAT_TOKEN_LAT = None

# ------------------- Helper Accessors -------------------
_IDLE_SESSION_TTL_SEC = 3600  # 1 h
_TEXT_PREWARM_SEMA = asyncio.Semaphore(int(os.getenv("TEXT_PREWARM_CONCURRENCY", "8")))


async def _prewarm_cached(url: str, paper_id: str) -> None:
    from services.text_extractor import TextExtractor

    async with _TEXT_PREWARM_SEMA:
        try:
            await TextExtractor.instance().ensure_cached(url, paper_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prewarm_failed", paper_id=paper_id, err=str(exc))


async def get_session_id(request: Request) -> str:  # FastAPI Depends helper
    return getattr(request.state, "session_id", "anon")


def get_llm_service() -> Optional[LLMService]:
    return llm_service_instance


def get_exa_client() -> Optional[ExaClient]:
    return exa_client_instance


def get_paper_service() -> TextExtractor:
    return TextExtractor.instance()


class DropSSE204Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        msg = record.getMessage()
        return not (" 204 " in msg and "/report/stream" in msg)


logging.getLogger("uvicorn.access").addFilter(DropSSE204Filter())

# ------------------- Loguru (application) logger ----------------
logger.remove()
logger.add(
    sys.stderr,
    level=config.config.LOG_LEVEL.upper(),
    serialize=config.config.LOG_FORMAT_JSON,
)

llm_service_instance: Optional[LLMService] = None
exa_client_instance: Optional[ExaClient] = None

KEEP_ALIVE_SEC = int(os.getenv("CHAT_KEEPALIVE_SEC", "1"))

# ------------------- Main Application Setup -------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global llm_service_instance, exa_client_instance
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
        logger.critical("Configuration error during startup: %s. Application cannot start.", ve)
        sys.exit(1)
    except LLMConfigurationError as llm_config_err:
        logger.critical(
            "LLM Configuration error during startup: %s. Application cannot start.", llm_config_err
        )
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "Critical error during service initialization: %s. Application cannot start.", e
        )
        sys.exit(1)
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

setup_observability(app)

try:
    from importlib import import_module

    _report_stream = import_module("routers.report_stream")
    _chat_modal = import_module("routers.chat_modal")

    app.include_router(_report_stream.router)
    app.include_router(_chat_modal.router)
except Exception as _imp_err:  # noqa: BLE001
    logger.warning("Could not import routers: %s", _imp_err)

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint  # noqa: E402


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:  # noqa: D401
        response: Response = await call_next(request)
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload"
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "clipboard-read=*, clipboard-write=*")
        return response


app.add_middleware(ChatSessionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

_static_path = _pathlib.Path(__file__).parent / "static"
if _static_path.exists():
    app.mount("/static", StaticFiles(directory=str(_static_path)), name="static")
else:
    logger.warning("Static directory not found at %s; static assets will return 404", _static_path)

# ------------------- Main Execution Block -------------------

if __name__ == "__main__":
    import uvicorn

    print(f"Starting Uvicorn server on http://{config.config.HOST}:{config.config.PORT}")
    uvicorn.run(
        "main:app",
        host=config.config.HOST,
        port=config.config.PORT,
        reload=config.config.RELOAD_APP,
        log_level="info",
    )

# Do not import `app` directly; use `lux_scholar.main` instead. Legacy
# `app.py` now re-exports this module so existing code continues to work.

import sys as _sys
from importlib import import_module as _imp

# Import the legacy implementation (still located at app.py for now)
_mod = _imp("app")
_sys.modules[__name__] = _mod
