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
from chat.state import _chat_states
from infra.exa_client import ExaClient
from infra.llm_service import LLMConfigurationError, LLMService
from infra.text.text_extractor import TextExtractor
from middleware.chat_session import ChatSessionMiddleware
from utils.observability import setup_observability

# Optional Prometheus histogram exported by `utils.observability`.
# It measures the **latency between consecutively streamed chat tokens** – a
# good proxy for perceived UI smoothness during SSE streaming.  It is `None`
# when metrics are disabled so downstream code can `if CHAT_TOKEN_LAT:` safely.
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:  # pragma: no cover
    CHAT_TOKEN_LAT = None

# ---------------------------------------------------------------------------
# Dependency-injection helpers (FastAPI ``Depends``)
# ---------------------------------------------------------------------------

_IDLE_SESSION_TTL_SEC = 3600  # 1 h
_TEXT_PREWARM_SEMA = asyncio.Semaphore(int(os.getenv("TEXT_PREWARM_CONCURRENCY", "8")))


async def _prewarm_cached(url: str, paper_id: str) -> None:
    """Fetch & cache a paper's full-text **in the background** after search.

    The *search* endpoint (``src/api/routes/search.py``) imports this helper
    and fires it via ``asyncio.create_task`` for each search result so when the
    user later asks to summarise a paper, its HTML/PDF is already parsed and
    cached on disk, cutting perceived latency by ~3-5s.
    """

    from infra.text.text_extractor import TextExtractor

    async with _TEXT_PREWARM_SEMA:  # Avoid hammering remote hosts concurrently.
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

# ---------------------------------------------------------------------------
# Bridge *all* third-party / stdlib logging → Loguru
# ---------------------------------------------------------------------------
# FastAPI / Uvicorn and many libraries still use the standard `logging` module.
# The intercept handler below reroutes those records so they are formatted by
# Loguru and share the same sinks/JSON structure as first-party logs.

class _InterceptHandler(logging.Handler):
    """Forward standard 'logging' records to *loguru* for uniform formatting."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        # Retrieve corresponding Loguru level (may be numeric or name).
        try:
            level = logger.level(record.levelname).name  # type: ignore[arg-type]
        except ValueError:
            level = record.levelno

        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(level, record.getMessage())


# Route the root logger through the intercept so third-party libs (uvicorn, fastapi, etc.)
# appear in our Loguru output.
logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

# ---------------------------------------------------------------------------
# Core application logger
# ---------------------------------------------------------------------------
# Remove the default Loguru sink and attach our single stderr sink so that:
# • Log level & JSON formatting honour runtime config.
# • There is one canonical place where logs leave the process (stdout/stderr).

logger.remove()
logger.add(
    sys.stderr,
    level=config.config.LOG_LEVEL.upper(),
    serialize=config.config.LOG_FORMAT_JSON,
)

llm_service_instance: Optional[LLMService] = None
exa_client_instance: Optional[ExaClient] = None

KEEP_ALIVE_SEC = int(os.getenv("CHAT_KEEPALIVE_SEC", "1"))

# ---------------------------------------------------------------------------
# FastAPI application set-up & dependency lifetime management
# ---------------------------------------------------------------------------

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

        gc_task = asyncio.create_task(_chatstate_gc_loop())
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
            if "gc_task" in locals():
                gc_task.cancel()
        else:
            logger.info("Application shutdown: LLM Service was not initialized.")


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
    pages as _pages_routes,
)
from api.routes import (
    report as _report_routes,
)
from api.routes import (
    search as _search_routes,
)

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
):
    app.include_router(_r)

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

# ---------------------------------------------------------------------------
# Static asset mounting (first matching path wins)
# ---------------------------------------------------------------------------

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
