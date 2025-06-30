import asyncio
import html
import logging
import os
import pathlib as _pathlib
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Optional, cast
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, FastAPI, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fasthtml.components import LI, UL, Div  # For dynamic list rendering (need LI for messages)
from loguru import logger

import config
from chat_runtime import (
    build_prompt,
    get_semaphore,  # needed for GC loop
)
from chat_state import ChatMessage, _chat_states, get_chat_state
from middleware.chat_session import ChatSessionMiddleware
from models.paper import Paper
from pages.main_page import (
    SharedPageData,
    home_page,
    render_chat_message,
    render_error_message,
    render_paper_card,
    render_report_config_form,
    render_search_results,
)
from services.exa_client import ExaClient
from services.llm_client import LLMConfigurationError, LLMService
from services.report_generator import ReportGenerator
from services.text_extractor import TextExtractor
from utils.date_utils import parse_user_date
from utils.observability import _METRICS_ENABLED, setup_observability

# CHAT_TOKEN_LAT is defined only when metrics enabled
try:
    from utils.observability import CHAT_TOKEN_LAT
except ImportError:
    CHAT_TOKEN_LAT = None

from utils.search_utils import clean_query

# --- Helper Accessors --------------------------------------------------

_IDLE_SESSION_TTL_SEC = 3600  # 1 h
_PAPER_CACHE_TTL_SEC = 60 * 60 * 24 * 7  # 7 days

# Limit concurrent TextExtractor pre-warms to avoid stampede
_TEXT_PREWARM_SEMA = asyncio.Semaphore(int(os.getenv("TEXT_PREWARM_CONCURRENCY", "8")))


async def _prewarm_cached(url: str, paper_id: str) -> None:
    from services.text_extractor import TextExtractor  # local import to avoid cycles

    async with _TEXT_PREWARM_SEMA:
        try:
            await TextExtractor.instance().ensure_cached(url, paper_id)
        except Exception as exc:
            log.warning("prewarm_failed", paper_id=paper_id, err=str(exc))


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


# ---------------------------------------------------------------------

# --- Configure Loguru Logger ---
# ------------------- Access-log noise filter --------------------
# uvicorn.access logs a line for every request.  After a server restart the
# browser may keep reconnecting to the defunct /report/stream token, leading to
# dozens of identical "204 No Content" lines.  Suppress those lines at the
# logger level to keep the console readable (no runtime overhead per request).


class _DropSSE204(logging.Filter):
    """Filter out boring 204 responses for /report/stream."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 – signature required by logging
        msg = record.getMessage()
        # Keep everything *except* 204 responses for the incremental report SSE.
        return not (" 204 " in msg and "/report/stream" in msg)


# Attach the filter to uvicorn's access logger
logging.getLogger("uvicorn.access").addFilter(_DropSSE204())

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
current_direct_query_task: asyncio.Task | None = None

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
                    log.info("chatstate_gc", evicted=removed)

        gc_task = asyncio.create_task(_chatstate_gc_loop())
        yield  # Application runs here
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

log = structlog.get_logger()

# Import routers lazily to avoid circular import during module initialization.
try:
    from importlib import import_module

    _report_stream = import_module("routers.report_stream")
    _chat_modal = import_module("routers.chat_modal")

    # Cast to APIRouter to satisfy static typing
    app.include_router(cast(APIRouter, _report_stream.router))
    app.include_router(cast(APIRouter, _chat_modal.router))
except Exception as _imp_err:  # noqa: BLE001 – log and continue startup
    logger.warning("Could not import routers: %s", _imp_err)

# Attach per-visitor session cookie middleware (Secure=True flag set)
app.add_middleware(ChatSessionMiddleware)

# --------------------------------------------------
# Static Files Mount (CSS, JS, images)
# --------------------------------------------------

_static_path = _pathlib.Path(__file__).parent / "static"
if _static_path.exists():
    app.mount("/static", StaticFiles(directory=str(_static_path)), name="static")
else:
    logger.warning("Static directory not found at %s; static assets will return 404", _static_path)

# --- Helper Functions ---


def _search_papers_in_thread(
    search_method: Callable[..., list[Paper]], **kwargs: Any
) -> list[Paper]:
    """Helper to execute blocking Exa requests in a thread.

    Args:
        search_method: The Exa search method to call.
        **kwargs: Keyword arguments for the search method.

    Returns:
        A list of Paper instances.
    """
    return cast(list[Paper], search_method(**kwargs))


# --- Route Handlers ---


@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request, search_query: Optional[str] = None) -> HTMLResponse:
    """Handles GET requests to the root URL.

    Optionally performs an initial search if 'search_query' is provided.

    Args:
        request: The incoming FastAPI request.
        search_query: The search query or URL from the request.

    Returns:
        An HTMLResponse containing the rendered home page.
    """
    logger.info(
        "get_home: Request received",
        endpoint="/",
        method="GET",
        params={"search_query": search_query},
    )
    page_data = SharedPageData(current_query=search_query)

    # Perform initial search if query is provided (e.g., from a bookmark or direct URL)
    if search_query and search_query.strip():
        if not config.config.EXA_API_KEY:
            logger.error("get_home: EXA_API_KEY not configured for initial search.")
            page_data.search_results = []
        else:
            try:
                assert exa_client_instance is not None
                logger.info(
                    "get_home: Performing initial Exa search",
                    query=search_query.strip(),
                    num_results=config.config.DEFAULT_PAPER_COUNT,
                )
                papers = await asyncio.to_thread(
                    exa_client_instance.search_papers,
                    query=search_query.strip(),
                    num_results=config.config.DEFAULT_PAPER_COUNT,  # Or a specific number for initial load
                )
                page_data.search_results = papers
            except Exception as e:
                logger.error(
                    "get_home: Error during initial Exa search",
                    query=search_query.strip(),
                    error=str(e),
                    exc_info=True,
                )
                page_data.search_results = []  # Ensure it's an empty list on error

    # Generate the full HTML page using the imported home_page component
    component_html = str(home_page(page_data=page_data))  # Uses imported home_page
    return HTMLResponse(content=component_html)


# --- Shuffle random paper Endpoint ---
from pages.main_page import _render_paper_item  # noqa: E402 – import after app init


@app.post("/shuffle", response_class=HTMLResponse)
async def shuffle_random_paper() -> HTMLResponse:
    """Return a new random wild-card paper from the cached pool.

    The response swaps the existing `#wild-card-item` list element via HTMX.

    Returns:
        An HTMLResponse with the new wild-card paper.
    """
    if exa_client_instance is None:
        return HTMLResponse(
            content="<li id='wild-card-item'>Server starting…</li>",
            headers={"HX-Reswap": "outerHTML"},
        )

    paper = cast(ExaClient, exa_client_instance).replace_wildcard()
    if paper is None:
        return HTMLResponse(
            content="<li id='wild-card-item'>No more random papers available. Run a new search.</li>",
            headers={"HX-Reswap": "outerHTML"},
        )

    rendered = str(Div(_render_paper_item(paper, wildcard=True)))
    return HTMLResponse(content=rendered)


@app.post("/shuffle_all", response_class=HTMLResponse)
async def shuffle_all_papers() -> HTMLResponse:
    """Shuffles all but the last (wild-card) paper in the current list.

    Returns:
        An HTMLResponse with the shuffled papers.
    """
    if exa_client_instance is None:
        return HTMLResponse("<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>")
    client = cast(ExaClient, exa_client_instance)

    papers = client.shuffle_k()
    if not papers:
        return HTMLResponse(
            content=str(
                Div(
                    UL(
                        LI(
                            "Cache was cleared (likely due to app reload). Please search again before shuffling.",
                            style="color: orange;",
                        ),
                        id="papers-list",
                    )
                )
            )
        )

    # Determine pinned id from cached search (if any)
    cached = client.get_cached_search()
    pinned_id = cached.pinned_id if cached is not None else None

    ul_items = []
    for idx, pap in enumerate(papers):
        ul_items.append(
            _render_paper_item(
                pap,
                is_main_paper=(pinned_id is not None and pap.id == pinned_id),
                wildcard=(idx == len(papers) - 1),
            )
        )
    ul_html = str(
        Div(UL(*ul_items, id="papers-list", style="list-style-type: none; padding-left: 0;"))
    )
    return HTMLResponse(content=ul_html)


@app.post("/reset_results", response_class=HTMLResponse)
async def reset_papers() -> HTMLResponse:
    """Resets the paper list to the original top-k + wild-card order.

    Returns:
        An HTMLResponse with the reset papers.
    """
    if exa_client_instance is None:
        return HTMLResponse("<ul id='papers-list'><li>Error: Exa client not initialized.</li></ul>")
    client = cast(ExaClient, exa_client_instance)

    core_list = client.reset_display()
    if not core_list:
        return HTMLResponse(
            content=str(
                Div(
                    UL(
                        LI(
                            "Cache was cleared (likely due to app reload). Please search again before resetting.",
                            style="color: orange;",
                        ),
                        id="papers-list",
                    )
                )
            )
        )

    cached = client.get_cached_search()
    pinned_id = cached.pinned_id if cached is not None else None

    ul_items = []
    for idx, pap in enumerate(core_list):
        ul_items.append(
            _render_paper_item(
                pap,
                is_main_paper=(pinned_id is not None and pap.id == pinned_id),
                wildcard=(idx == len(core_list) - 1),
            )
        )
    ul_html = str(
        Div(UL(*ul_items, id="papers-list", style="list-style-type: none; padding-left: 0;"))
    )
    return HTMLResponse(content=ul_html)


# --- Ask LLM Endpoint ---
@app.post("/ask-llm", response_class=HTMLResponse)
async def ask_llm(
    request: Request,
    question: str = Form(...),
    audience: str = Form("technical"),
    summary_size: str = Form("M"),
) -> HTMLResponse:
    """Direct question-answering using the LLM without paper search.

    This wraps ReportGenerator.generate_direct_query_answer() for convenience.
    """
    logger.info(
        "ask_llm: Request received",
        endpoint="/ask-llm",
        method="POST",
        params={"question": question},
    )

    if llm_service_instance is None or not llm_service_instance._initialized:
        logger.error("LLM service not initialized for /ask-llm")
        return HTMLResponse(
            content="<p>Server error: LLM service unavailable.</p>", status_code=500
        )

    # Use default model from config for now
    default_model_key = config.config.DEFAULT_MODEL_KEY
    model_info = config.config.AVAILABLE_MODELS[default_model_key]

    rg = ReportGenerator(
        llm_service=llm_service_instance,
        exa_client=cast(ExaClient, exa_client_instance),
        selected_model_api_id=model_info["id"],
        selected_model_provider=model_info["provider"],
        selected_model_input_token_limit=model_info["input_token_limit"],
    )

    try:
        # Map summary_size to max_tokens (S≈256, M≈512, L≈1024)
        size_map = {"S": 256, "M": 512, "L": 1024}
        max_tokens = size_map.get(summary_size.upper(), 512)

        answer_html = await rg.generate_direct_query_answer(
            query=question.strip(), audience_type=audience, max_tokens=max_tokens
        )
        return HTMLResponse(content=answer_html)
    except Exception as e:  # noqa: BLE001
        logger.exception("ask_llm failed", error=str(e))
        return HTMLResponse(
            content=f"<div class='error-banner'>Error: {html.escape(str(e))}</div>",
            status_code=500,
        )


# --- Chat Endpoints --------------------------------------------------------


@app.post("/chat")
async def chat_post(
    request: Request,
    session_id: str = Depends(get_session_id),
    message: str = Form(...),
    paper_id: str = Form(...),
) -> Response:
    """Accepts a user's message, stores it, and returns two things:
    1. The user's message, rendered as a chat bubble.
    2. An empty container for the assistant's response, equipped with SSE
       attributes to connect to the streaming endpoint.
    """
    chat_state = get_chat_state(session_id)

    # If a stream is already active, signal busy to frontend and let HTMX retry.
    if chat_state.stream_active and chat_state.last_role(paper_id) == "assistant":
        chat_state.busy_retries += 1
        if chat_state.busy_retries >= 6:
            chat_state.busy_retries = 0  # reset after escalation
            return Response("Assistant still busy – please try again later.", status_code=429)
        busy_banner = (
            '<div id="status-banner" hx-swap-oob="innerHTML:#status-banner">'
            "Assistant busy – retrying…"
            "</div>"
        )
        return HTMLResponse(content=busy_banner, status_code=202)

    # reset counter on successful accept
    chat_state.busy_retries = 0

    clean_msg = message.strip()
    # Ignore empty/whitespace-only submits – return 204 so HTMX no-ops.
    if not clean_msg:
        return Response(status_code=204)

    chat_state.append(paper_id, ChatMessage(role="user", content=clean_msg))
    log.info("chat_request", session_id=session_id, paper_id=paper_id, chars=len(clean_msg))
    # Reset ring buffer for new assistant response
    chat_state.reset_stream_buffer()
    chat_state.paper_cache.setdefault(paper_id, {})

    # 1. Render the user's message immediately
    user_message_html = str(render_chat_message(clean_msg, "user"))

    # 2. Create a unique container for the assistant's streaming response (raw HTML)
    assistant_response_id = f"assistant-response-{uuid4()}"
    # Let custom JS handle token insertion. We keep the SSE connection but disable HTMX's
    # automatic DOM swapping to avoid duplicate tokens.
    assistant_response_container_html = (
        f'<div id="{assistant_response_id}" class="assistant-message typing" '
        f'hx-ext="sse" sse-connect="/chat/stream?paper_id={paper_id}" '
        f'sse-swap="message" data-batch-ms="8" hx-sse-reconnect-delay="-1" '
        f'hx-trigger="load">…</div>'
    )

    # Combine and return as a multi-part HTML response
    full_response_html = user_message_html + assistant_response_container_html
    return HTMLResponse(content=full_response_html)


@app.get("/chat/stream")
async def chat_stream(
    request: Request,
    paper_id: str,
    resume_at: int = Query(0, ge=0, description="Token index to resume from."),
) -> Response:
    """Endpoint that streams the LLM's response using SSE."""
    session_id = await get_session_id(request)
    if not session_id:
        return Response("Session not found", status_code=400)

    # Ensure services are available
    llm_service = get_llm_service()
    paper_service = get_paper_service()
    if not llm_service or not paper_service:
        return Response("Services not available", status_code=503)

    chat_state = get_chat_state(session_id)

    # Resume requested but nothing to replay – fast-fail with 204 so HTMX opens a fresh stream.
    if resume_at and chat_state.stream_buffer:
        last_idx = chat_state.stream_buffer[-1][0]
        if resume_at >= last_idx:
            log.info(
                "chat_resume_past_tail", session_id=session_id, requested=resume_at, last=last_idx
            )
            return Response(status_code=204)

    # Reject any concurrent stream attempt (HTMX reconnect or duplicate request)
    if chat_state.stream_active:
        return Response(status_code=204)
    chat_state.stream_active = True

    async def event_generator() -> AsyncIterator[str]:
        try:
            completed_successfully = False  # tracks whether LLM finished naturally
            # Ensure `assistant_msg` is always defined so we can safely log its length in `finally`
            assistant_msg: str = ""

            # If client requests resume and we have tokens buffered, replay them.
            if resume_at > 0 and chat_state.stream_buffer:
                for idx, tok in chat_state.stream_buffer:
                    if idx >= resume_at:
                        safe_tok = html.escape(tok).replace("\n", "<br/>")
                        yield f"data: {safe_tok}\n\n"
                # Log completion of replay for observability
                log.info(
                    "chat_resume_replay_complete", session_id=session_id, replay_start=resume_at
                )
                # After replaying, exit – client will open a fresh stream for new tokens
                return

            async def _client_gone() -> bool:
                """Return True if the browser closed the EventSource; also clear flag."""
                if await request.is_disconnected():
                    log.info("client_disconnect_pre_llm", session_id=session_id)
                    chat_state.stream_active = False
                    return True
                return False

            # Bail out immediately if user already clicked Stop before backend work.
            if await _client_gone():
                yield "event: sse-close\ndata: \n\n"
                return

            # Ensure paper full text is cached and fresh once per stream
            def _needs_refresh() -> bool:
                """Return True when we should (re)cache the paper full text."""
                if paper_id not in chat_state.paper_cache:
                    return True
                path = chat_state.paper_cache.get(paper_id, {}).get("full_text_path")
                if not path:
                    return True
                try:
                    age = time.time() - _pathlib.Path(path).stat().st_mtime
                    return age > _PAPER_CACHE_TTL_SEC
                except FileNotFoundError:
                    # Cached file was deleted between runs → refresh
                    return True

            if _needs_refresh():
                path = await paper_service.get_paper_content_from_id(paper_id)
                if path:
                    chat_state.paper_cache.setdefault(paper_id, {})["full_text_path"] = path

            # User may have stopped while we were fetching/excerpting
            if await _client_gone():
                yield "event: sse-close\ndata: \n\n"
                return

            # Send a keep-alive ping every 15s to prevent connection drops
            # on some hosting platforms (e.g., Heroku, Vercel)
            last_tok_t = time.perf_counter()
            while True:
                try:
                    # Check if the client has disconnected
                    if await request.is_disconnected():
                        print("Client disconnected, closing stream.")
                        break

                    # If there's a new user message to process...
                    if chat_state.last_role(paper_id) == "user":
                        prompt = await build_prompt(chat_state, paper_id)
                        async with get_semaphore(session_id, paper_id):
                            stream_iter = await llm_service.generate_completion_stream(
                                provider_name=config.config.AVAILABLE_MODELS[
                                    config.config.DEFAULT_MODEL_KEY
                                ]["provider"],
                                model_name=config.config.AVAILABLE_MODELS[
                                    config.config.DEFAULT_MODEL_KEY
                                ]["id"],
                                prompt=prompt,
                                max_tokens=int(os.getenv("CHAT_MAX_TOKENS", "512")),
                                temperature=float(os.getenv("CHAT_TEMPERATURE", "0.7")),
                                system_prompt=None,  # included in prompt text already
                            )
                            async for token in stream_iter:
                                # Abort if client closed SSE connection
                                if await request.is_disconnected():
                                    if hasattr(stream_iter, "aclose"):
                                        # Fire-and-forget shutdown so we don't block on network latency
                                        asyncio.create_task(stream_iter.aclose())
                                    completed_successfully = False
                                    print("Client disconnected mid-stream – cancelling generation.")
                                    break

                                assistant_msg += token
                                log.debug("chat_stream_token", session_id=session_id, t=len(token))
                                chat_state.push_token(token)
                                safe_token = html.escape(token).replace("\n", "<br/>")
                                yield f"data: {safe_token}\n\n"
                                if _METRICS_ENABLED:
                                    now_t = time.perf_counter()
                                    CHAT_TOKEN_LAT.observe(now_t - last_tok_t)
                                    last_tok_t = now_t

                            # If loop exited normally, mark completion
                            if completed_successfully is False:
                                completed_successfully = True
                                log.info(
                                    "chat_stream_complete",
                                    session_id=session_id,
                                    tokens=len(assistant_msg),
                                )

                            # Exit the outer keep-alive loop so the stream can finalise
                            break

                    # If no new message (or after breaking), just wait before sending next keep-alive
                    await asyncio.sleep(KEEP_ALIVE_SEC)
                    yield ": keep-alive\n\n"

                except asyncio.CancelledError:
                    print("Stream cancelled by client.")
                    # Client closed connection – treat as aborted; skip save
                    completed_successfully = False
                    break
                except Exception as e:
                    print(f"Error in chat stream: {e}")
                    error_message = html.escape("Sorry, an error occurred.")
                    yield "event: error\n"  # send named event so JS can style
                    yield f"data: {error_message}\n\n"
                    break
        finally:
            log.info("chat_stream_complete", session_id=session_id, tokens=len(assistant_msg))
            # Persist only if the stream finished without cancellation
            if completed_successfully and assistant_msg:
                chat_state.append(paper_id, ChatMessage(role="assistant", content=assistant_msg))
            else:
                # Discard any partially buffered tokens
                chat_state.reset_stream_buffer()

            # Mark stream inactive *before* attempting to emit closing events so
            # that even if the client has already disconnected (BrokenPipe) the
            # flag is cleared and the next /chat/stream request is accepted.
            chat_state.stream_active = False

            # Attempt to notify client of normal end; ignore any BrokenPipe
            # raised by a vanished EventSource.
            try:
                # Use a simple 'done' event so frontend can deterministically
                # finalise the message, then the HTMX-specific 'sse-close'.
                yield "event: done\ndata: \n\n"
                yield "event: sse-close\ndata: \n\n"
            except Exception:
                # Client already gone – safe to ignore.
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Health-check Endpoint ---
@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    """Simple liveness probe for deployment platforms.

    Returns:
        A dictionary with a single key 'ok' set to True.
    """
    return {"ok": True}


@app.post("/search", response_class=HTMLResponse)
async def handle_search(
    request: Request,
    search_query: str = Form(...),
    start_published_date: Optional[str] = Form(None),
    num_results: Optional[int] = Form(config.config.DEFAULT_PAPER_COUNT),
    mode: Optional[str] = Form("auto"),  # 'auto' (default), 'direct'
    domain_filter: Optional[str] = Form(None),  # New optional domain filter from UI
) -> HTMLResponse:
    """Handles POST requests from the search form (HTMX).

    Performs a search using the Exa API based on form parameters.

    Args:
        request: The incoming FastAPI request.
        search_query: The search query or URL from the form.
        start_published_date: The start date for filtering papers.
        num_results: The number of results to return.
        mode: The search mode ('auto' or 'direct').
        domain_filter: The domain filter for the search.

    Returns:
        An HTMLResponse containing the search results or an error message.
    """
    global current_direct_query_task  # Allow modification of the global variable

    logger.info(
        "handle_search: Request received",
        endpoint="/search",
        method="POST",
        params={
            "search_query": search_query,
            "start_published_date": start_published_date,
            "num_results": num_results,
            "mode": mode,
            "domain_filter": domain_filter,
        },
    )
    # logger.info(f"Received search query: '{search_query}', date filter: {start_published_date}, num_results: {num_results}") # Old log

    if not config.config.EXA_API_KEY:
        logger.error("handle_search: Exa API key not configured", endpoint="/search")
        error_html = render_error_message(
            "The search service is not configured. Please ensure the API key is set up.",
            context="exa_api_key_missing",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"},
        )

    input_query_stripped = clean_query(search_query.strip())

    # --- Task Cancellation Logic ---
    if mode != "direct":  # A "Search Papers" or URL search request
        if current_direct_query_task and not current_direct_query_task.done():
            logger.info("Paper search initiated. Cancelling active direct LLM query task.")
            current_direct_query_task.cancel()
            current_direct_query_task = None  # Clear the reference
    else:  # A new "direct" (Ask LLM) request
        if current_direct_query_task and not current_direct_query_task.done():
            logger.info(
                "New direct LLM query initiated. Cancelling previous active direct LLM query task."
            )
            current_direct_query_task.cancel()
            # The cancelled task will raise CancelledError internally and should be handled by its awaiter.
            current_direct_query_task = None  # Clear the reference to the old task

    # --- Direct Query Short-circuit --------------------------------------
    if mode == "direct":
        # Ensure this specific task instance is tracked for its finally block
        this_direct_query_task_instance: asyncio.Task | None = None
        try:
            # Ensure LLM service ready
            if llm_service_instance is None or not llm_service_instance._initialized:
                logger.error("LLM service not initialized for direct query", endpoint="/search")
                return HTMLResponse(
                    content="<p>Server error: LLM service unavailable.</p>", status_code=500
                )

            # Default model from config; could be parameterised later
            default_model_key = config.config.DEFAULT_MODEL_KEY
            model_info = config.config.AVAILABLE_MODELS[default_model_key]

            rg = ReportGenerator(
                llm_service=llm_service_instance,
                exa_client=cast(ExaClient, exa_client_instance),
                selected_model_api_id=model_info["id"],
                selected_model_provider=model_info["provider"],
                selected_model_input_token_limit=model_info["input_token_limit"],
            )

            async def _direct_query_logic() -> str:
                # This is the actual async work for the direct query
                return await rg.generate_direct_query_answer(query=input_query_stripped)

            # Create and store the new task
            this_direct_query_task_instance = asyncio.create_task(_direct_query_logic())
            current_direct_query_task = this_direct_query_task_instance  # Update global reference

            answer_html = await this_direct_query_task_instance
            return HTMLResponse(content=answer_html)
        except asyncio.CancelledError:
            logger.info(
                f"Direct LLM query task (id: {this_direct_query_task_instance}) was cancelled."
            )
            # If this task was cancelled, it means either a new paper search or a new direct query
            # has taken precedence. The UI will be updated by that new request.
            # Return a minimal response or an empty one if the new request handles UI update.
            return HTMLResponse(content="<p><i>LLM query cancelled.</i></p>")
        except Exception as e:
            logger.exception("Direct query failed", error=str(e))
            return HTMLResponse(
                content=f"<div class='error-banner'>Error: {html.escape(str(e))}</div>",
                status_code=500,
            )
        finally:
            # If the task that ran (or was cancelled) in this handler is the current global task,
            # and it's done, clear the global reference.
            if (
                this_direct_query_task_instance
                and current_direct_query_task is this_direct_query_task_instance
                and this_direct_query_task_instance.done()
            ):
                current_direct_query_task = None
                logger.debug(
                    f"Cleared global reference to completed/cancelled direct query task (id: {this_direct_query_task_instance})"
                )

    # is_url_search_result = False # Default to false # This flag will be set explicitly

    try:
        assert exa_client_instance is not None
        # papers_data: List[Paper] = [] # Will be structured differently for URL searches
        results_html_fragment: str = ""

        parsed_start_date: datetime | None = None
        if start_published_date and start_published_date.strip():
            parsed_start_date = parse_user_date(start_published_date)
            if not parsed_start_date:
                logger.warning(
                    "handle_search: Invalid start_published_date format",
                    date_received=start_published_date,
                )
                error_html = render_error_message(
                    f"Invalid date format: '{start_published_date}'. Please use YYYY-MM-DD or MM/DD/YYYY.",
                    context="invalid_date_format",
                ).render()
                return HTMLResponse(
                    content=error_html,
                    headers={
                        "HX-Retarget": "#search-results-container",
                        "HX-Reswap": "innerHTML",
                    },
                )

        # Shared keyword arguments for both search types
        shared_kwargs: dict[str, Any] = {}
        if parsed_start_date:
            shared_kwargs["start_published_date"] = parsed_start_date
        if domain_filter and domain_filter.lower() not in {"any", "all"}:
            shared_kwargs["include_domains"] = [domain_filter]

        # Determine if the query is a URL or a search term.
        if exa_client_instance.is_url(input_query_stripped):
            is_url_search_flag = True  # URL input
            logger.info(
                "handle_search: URL detected, using unified url_search_and_cache",
                input_type="url",
                url=input_query_stripped,
                k=num_results,
                filters=shared_kwargs,
            )

            # For URL searches, papers_data is a dict with 'main_paper' and 'similar_papers'.
            # The Exa client returns a list of Paper objects directly.
            exa_results = cast(ExaClient, exa_client_instance).url_search_and_cache(
                paper_url=input_query_stripped,
                k=num_results if num_results is not None else config.config.DEFAULT_PAPER_COUNT,
                **shared_kwargs,
            )
            logger.info(
                "handle_search: url_search_and_cache successful",
                input_type="url",
                url=input_query_stripped,
                papers_found=len(exa_results),
            )

            results_component = render_search_results(
                papers_list=exa_results,
                is_url_search=is_url_search_flag,
                original_query=input_query_stripped,
            )
            results_html_fragment = str(results_component)

            # Background pre-warm of full-text cache for top search results (URL search)
            for paper in exa_results[:5]:  # limit to a few to avoid stampede
                asyncio.create_task(_prewarm_cached(str(paper.url), paper.id))
        else:  # This is a topic/keyword search
            is_url_search_flag = False
            logger.info(
                "handle_search: Query detected, searching papers",
                input_type="query",
                params={
                    "query": input_query_stripped,
                    "num_results": num_results
                    if num_results is not None
                    else config.config.DEFAULT_PAPER_COUNT,
                    "use_autoprompt": True,
                    "category": "research_paper",
                },
            )

            # Kwargs forwarded to ExaClient.search_and_cache (excluding *query* and *k*)
            search_kwargs: dict[str, Any] = {
                "use_autoprompt": True,
                "category": "research_paper",
            }
            search_kwargs.update(shared_kwargs)

            # For non-URL searches, papers_data is a flat list.
            # render_search_results expects 'papers_list' for this case.
            # Assign method to a variable after assertion to help Mypy
            exa_results = cast(ExaClient, exa_client_instance).search_and_cache(
                query=input_query_stripped,
                k=num_results if num_results is not None else config.config.DEFAULT_PAPER_COUNT,
                **search_kwargs,
            )
            logger.info(
                "handle_search: Exa search_and_cache successful",
                input_type="query",
                query=input_query_stripped,
                papers_found=len(exa_results),
            )

            results_component = render_search_results(
                papers_list=exa_results,
                is_url_search=is_url_search_flag,
                original_query=input_query_stripped,  # Pass the original query text
            )
            results_html_fragment = str(results_component)

            # Background pre-warm of full-text cache for top search results (keyword search)
            for paper in exa_results[:5]:
                asyncio.create_task(_prewarm_cached(str(paper.url), paper.id))

        return HTMLResponse(content=results_html_fragment)
    except Exception as e:
        logger.exception(
            "handle_search: Error during Exa API call", query=input_query_stripped, error=str(e)
        )
        error_html = render_error_message(
            "Could not retrieve search results. An unexpected error occurred. Please try again later.",
            context="exa_api_error",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"},
        )


@app.post("/show-report-config", response_class=HTMLResponse)
async def show_report_config(
    request: Request,
    original_search_query_for_report: str = Form(...),
    selected_papers_json: Optional[list[str]] = Form(None),  # noqa: B008
    is_url_input_source: Optional[str] = Form(None),  # Added to receive the flag
) -> HTMLResponse:
    """Handles POST requests to show the report configuration form.

    This endpoint is triggered when the user clicks 'Configure Report'.

    Args:
        request: The incoming FastAPI request.
        original_search_query_for_report: The initial search query or URL.
        selected_papers_json: A JSON string of selected paper IDs.
        is_url_input_source: A flag indicating if the original query was a URL.

    Returns:
        An HTMLResponse with the report configuration form or an error message.
    """
    is_url = is_url_input_source == "true"  # Convert to boolean
    num_papers_received = len(selected_papers_json) if selected_papers_json else 0

    form_data = await request.form()
    logger.info(
        "show_report_config: Received request to show report config",
        params={
            "original_search_query": original_search_query_for_report,
            "selected_papers_json_count": num_papers_received,
            "is_url_input_source": is_url_input_source,
            "raw_form_data": dict(form_data),  # Log all form fields
        },
    )

    if not selected_papers_json:
        logger.warning(
            "show_report_config: No papers selected for report configuration",
            query=original_search_query_for_report,
            is_url=is_url,
        )
        # Render the form but indicate no papers were selected, or show an error/message.
        # For consistency with render_report_config_form's own handling:
        config_form_component = render_report_config_form(
            original_query=original_search_query_for_report,
            selected_papers_json_list=[],
            num_selected=0,
            is_url_input=is_url,  # Pass the flag
        )
        return HTMLResponse(content=str(config_form_component))

    deserialized_papers: list[Paper] = []
    try:
        for paper_json_str in selected_papers_json:
            deserialized_papers.append(Paper.model_validate_json(paper_json_str))
        logger.info(
            "show_report_config: Successfully deserialized papers",
            num_deserialized=len(deserialized_papers),
            query=original_search_query_for_report,
        )
    except Exception as e:
        logger.exception(
            "show_report_config: Error deserializing paper JSON",
            query=original_search_query_for_report,
            num_papers_json=num_papers_received,
            error=str(e),
        )
        error_html = render_error_message(
            "Could not process the selected paper data. Please try selecting papers again.",
            context="paper_deserialization_error_show_config",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"},
        )

    if not deserialized_papers:
        logger.warning(
            "show_report_config: No papers provided for report config after deserialization attempt (or initially empty)",
            query=original_search_query_for_report,
            is_url=is_url,
        )
        # This case might be redundant if the initial check for `not selected_papers_json` handles it,
        # but kept for robustness if deserialization results in an empty list from non-empty input.
        error_html = render_error_message(
            "No valid paper data was processed. Please select papers again.",
            context="no_valid_papers_processed",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"},
        )

    logger.info(
        "show_report_config: Rendering report config form with papers",
        query=original_search_query_for_report,
        num_papers=len(deserialized_papers),
    )

    config_form_component = render_report_config_form(
        original_query=original_search_query_for_report,
        selected_papers_json_list=selected_papers_json,  # Pass the original JSON strings
        num_selected=len(deserialized_papers),
        is_url_input=is_url,  # Pass the flag
    )
    return HTMLResponse(content=str(config_form_component))


@app.post("/generate-report", response_class=HTMLResponse)
async def generate_report(
    request: Request,
    original_query_for_report: str = Form(...),
    selected_papers_json: list[str] = Form(...),  # noqa: B008
    audience_type: str = Form(...),
    summary_size: str = Form(...),
    selected_model_key: str = Form(...),
    is_url_input: bool = Form(False),  # Changed to bool, FastAPI handles conversion
) -> HTMLResponse:
    """Handles POST requests to generate the actual report.

    Uses the application-scoped LLMService instance.

    Args:
        request: The incoming FastAPI request.
        original_query_for_report: The initial search query or URL.
        selected_papers_json: A JSON string of selected paper IDs.
        audience_type: The target audience for the report.
        summary_size: The desired size of the summary.
        selected_model_key: The selected model key.
        is_url_input: A flag indicating if the original query was a URL.

    Returns:
        An HTMLResponse with the generated report or an error message.
    """
    num_papers_received = len(selected_papers_json) if selected_papers_json else 0
    log_params = {
        "original_query_for_report": original_query_for_report,
        "num_selected_papers": num_papers_received,
        "audience_type": audience_type,
        "summary_size": summary_size,
        "selected_model_key": selected_model_key,
        "is_url_input": is_url_input,
    }
    logger.info(
        "generate_report: Request received",
        endpoint="/generate-report",
        method="POST",
        params=log_params,
    )

    if llm_service_instance is None or not llm_service_instance._initialized:
        logger.critical(
            "generate_report: LLMService not initialized. Cannot generate report.",
            params=log_params,
        )
        error_html = render_error_message(
            "The report generation service is currently unavailable. Please try again later or contact support.",
            context="llm_service_unavailable",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Case-insensitive lookup for the model key
    actual_model_key_in_config = None
    for key_in_config in config.config.AVAILABLE_MODELS:
        if key_in_config.lower() == selected_model_key.lower():
            actual_model_key_in_config = key_in_config
            break

    if not actual_model_key_in_config:
        logger.error(
            f"generate_report: Invalid model key '{selected_model_key}' provided.",
            params=log_params,
        )
        error_html = render_error_message(
            f"The selected model '{html.escape(selected_model_key)}' is not recognized. Please choose a valid model.",
            context="invalid_model_key",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    selected_model_details = config.config.AVAILABLE_MODELS[actual_model_key_in_config]
    selected_model_api_id = selected_model_details["id"]
    selected_model_provider = selected_model_details["provider"]
    selected_model_input_token_limit = selected_model_details["input_token_limit"]

    try:
        deserialized_papers = [Paper.model_validate_json(p_json) for p_json in selected_papers_json]
    except Exception as e:
        logger.exception(
            "generate_report: Error deserializing paper JSON",
            error=str(e),
            num_papers_json=num_papers_received,
            params=log_params,
        )
        error_html = render_error_message(
            "Could not process paper data. Please try selecting papers again.",
            context="paper_deserialization_error",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    if not deserialized_papers:
        logger.warning(
            "generate_report: No papers provided for report generation after deserialization",
            params=log_params,
        )
        error_html = render_error_message(
            "No papers were selected to generate a report.", context="no_papers_for_report"
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Ensure exa_client_instance is available (initialized in lifespan)
    global exa_client_instance
    if exa_client_instance is None:
        logger.critical(
            "generate_report: Exa Service not available. Cannot generate report.", params=log_params
        )
        error_html = render_error_message(
            "The paper information service is currently unavailable. Please try again later or contact support.",
            context="exa_service_unavailable",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    # Prepare paper lists based on input type
    main_paper_for_report: Optional[Paper] = None
    similar_papers_for_report: list[Paper] = []
    topic_papers_for_report: list[Paper] = []

    if is_url_input:
        # Identify the main paper (matching the original URL query) and other selected similar papers
        for paper in deserialized_papers:
            if paper.url == original_query_for_report:
                main_paper_for_report = paper
            else:
                similar_papers_for_report.append(paper)
        # It's possible the user didn't select the main paper, so main_paper_for_report could be None
        # It's also possible they only selected the main paper, so similar_papers_for_report could be empty
        logger.info(
            "generate_report: URL input - Main paper for report identified",
            main_paper_title=main_paper_for_report.title
            if main_paper_for_report
            else "None selected/found",
            num_similar_selected=len(similar_papers_for_report),
        )
    else:
        # For topic/keyword searches, all deserialized papers are for the topic report
        topic_papers_for_report = deserialized_papers
        logger.info(
            "generate_report: Topic input - Papers for report",
            num_papers=len(topic_papers_for_report),
        )

    # Check if there's anything to summarize, especially for URL input
    if is_url_input and not main_paper_for_report and not similar_papers_for_report:
        logger.warning(
            "generate_report: URL input but no main or similar papers selected for the report.",
            params=log_params,
        )
        error_html = render_error_message(
            "No papers were selected from the URL search results to generate a report.",
            context="no_url_papers_for_report",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )
    elif (
        not is_url_input and not topic_papers_for_report
    ):  # Should be caught by earlier deserialized_papers check, but for safety
        logger.warning(
            "generate_report: Topic input but no papers for the report.", params=log_params
        )
        error_html = render_error_message(
            "No papers were selected to generate a report.", context="no_topic_papers_for_report"
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )

    try:
        # Correctly instantiate ReportGenerator with all required parameters
        report_generator = ReportGenerator(
            llm_service=llm_service_instance,
            exa_client=exa_client_instance,
            selected_model_api_id=selected_model_api_id,
            selected_model_provider=selected_model_provider,
            selected_model_input_token_limit=selected_model_input_token_limit,
        )

        # Call generate_report_html with correct parameters
        report_html = await report_generator.generate_report_html(
            query_or_url=original_query_for_report,
            papers_to_summarize=deserialized_papers,
            audience_type=audience_type,
            summary_size=summary_size,
            is_url_input=is_url_input,
            main_input_paper=main_paper_for_report,
            selected_similar_papers_data=similar_papers_for_report,
            papers_for_topic_report=topic_papers_for_report,
        )
        return HTMLResponse(content=report_html)
    except LLMConfigurationError as e:
        logger.error(
            "generate_report: LLM Configuration Error during report generation",
            error=str(e),
            params=log_params,
            exc_info=True,
        )
        error_html = render_error_message(
            f"LLM Configuration Error: {e}. Please check API keys and model settings.",
            context="llm_config_error_report_gen",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )
    except Exception as e:
        logger.exception(
            "generate_report: Unexpected error during report generation",
            error=str(e),
            params=log_params,
        )
        error_html = render_error_message(
            f"An unexpected error occurred while generating the report: {e}",
            context="unexpected_report_gen_error",
        ).render()
        return HTMLResponse(
            content=error_html,
            headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"},
        )


@app.get("/papers/{paper_id}/card")
async def citation_card(paper_id: str) -> Response:
    """Returns small hover card HTML for a paper citation."""
    # For now, look in cached search results; could extend later
    # Use simple sessionless search list in memory if needed
    # Fallback minimal card
    paper = None
    # TODO: Implement lookup for paper details based on current session cache
    if not paper:
        return HTMLResponse(
            content=f"<div style='padding:6px;font-size:0.8em;'>Paper {paper_id}</div>"
        )
    return HTMLResponse(content=str(render_paper_card(paper)))


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
