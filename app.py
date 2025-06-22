import asyncio
import html
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Optional, cast

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fasthtml.components import LI, UL, Div  # For dynamic list rendering (need LI for messages)
from loguru import logger

import config
from models.paper import Paper
from pages.main_page import (
    SharedPageData,
    home_page,
    render_error_message,
    render_report_config_form,
    render_search_results,
)
from services.exa_client import ExaClient
from services.llm_client import LLMConfigurationError, LLMService
from services.report_generator import ReportGenerator
from utils.search_utils import clean_query

# --- Configure Loguru Logger ---
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
        else:
            logger.info("Application shutdown: LLM Service was not initialized.")


app = FastAPI(lifespan=lifespan)

# --- Helper Functions ---


def _search_papers_in_thread(
    search_method: Callable[..., list[Paper]], **kwargs: Any
) -> list[Paper]:
    """Helper to execute blocking Exa requests in a thread."""
    return cast(list[Paper], search_method(**kwargs))


# --- Route Handlers ---


@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request, search_query: Optional[str] = None) -> HTMLResponse:
    """
    Handles GET requests to the root URL.
    Optionally performs an initial search if 'search_query' is provided.
    Renders the main home page.
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
    """Shuffles all but the last (wild-card) paper in the current list."""
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
    """Resets the paper list to the original top-k + wild-card order."""
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


# --- Health-check Endpoint ---
@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    """Simple liveness probe for deployment platforms."""
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
    """
    Handles POST requests from the search form (HTMX).
    Performs a search using the Exa API based on form parameters.
    Returns an HTML fragment of the search results.
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
            return HTMLResponse(content=f"<p>Error: {e}</p>", status_code=500)
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

        if exa_client_instance.is_url(input_query_stripped):
            is_url_search_flag = True  # URL input
            logger.info(
                "handle_search: URL detected, using unified url_search_and_cache",
                input_type="url",
                params={
                    "paper_url": input_query_stripped,
                    "num_results": num_results
                    if num_results is not None
                    else config.config.DEFAULT_PAPER_COUNT,
                },
            )

            # Fetch input paper + similar papers and cache
            exa_results = cast(ExaClient, exa_client_instance).url_search_and_cache(
                paper_url=input_query_stripped,
                k=num_results if num_results is not None else config.config.DEFAULT_PAPER_COUNT,
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

            search_params = {
                "query": input_query_stripped,
                "num_results": num_results
                if num_results is not None
                else config.config.DEFAULT_PAPER_COUNT,
                "use_autoprompt": True,
                "category": "research_paper",
            }

            if start_published_date and start_published_date.strip():
                try:
                    # Accept either YYYY-MM-DD or MM/DD/YYYY from the date picker
                    cleaned_date = start_published_date.strip()

                    parsed_dt: datetime | None = None
                    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
                        try:
                            parsed_dt = datetime.strptime(cleaned_date, fmt)
                            break
                        except ValueError:
                            continue

                    if parsed_dt is None:
                        raise ValueError("Unrecognized date format")

                    # Use full ISO date with Z to satisfy Exa filtering
                    iso_date = parsed_dt.strftime("%Y-%m-%dT00:00:00Z")
                    search_params["start_published_date"] = iso_date
                    logger.info(
                        f"handle_search: Using date filter for search: {search_params['start_published_date']}"
                    )
                except ValueError:
                    logger.warning(
                        "handle_search: Invalid start_published_date format",
                        date_received=start_published_date,
                        error="Ignoring date filter.",
                    )
                    error_html = render_error_message(
                        f"Invalid date format: '{start_published_date}'. Please use YYYY-MM-DD or MM/DD/YYYY format.",
                        context="invalid_date_format",
                    ).render()
                    return HTMLResponse(
                        content=error_html,
                        headers={
                            "HX-Retarget": "#search-results-container",
                            "HX-Reswap": "innerHTML",
                        },
                    )

            # Author filter support removed as per requirements
            # authors_list: list[str] | None = None
            # if authors:
            #     authors_list = [a.strip() for a in authors.split(",") if a.strip()]
            #     if authors_list:
            #         search_params["authors"] = authors_list

            # For non-URL searches, papers_data is a flat list.
            # render_search_results expects 'papers_list' for this case.
            # Assign method to a variable after assertion to help Mypy
            exa_results = cast(ExaClient, exa_client_instance).search_and_cache(
                query=input_query_stripped,
                k=num_results if num_results is not None else config.config.DEFAULT_PAPER_COUNT,
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
    """
    Handles POST requests to show the report configuration form.
    This endpoint is triggered when the user clicks 'Configure Report'.
    It receives the selected papers and other context to build the config form.
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
    """
    Handles POST requests to generate the actual report.
    Uses the application-scoped LLMService instance.
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


# --- Main Execution Block ---

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
