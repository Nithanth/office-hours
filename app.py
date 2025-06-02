from fastapi import FastAPI, Request, Form as FastAPIForm
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles 
from contextlib import asynccontextmanager

from typing import List, Optional, AsyncIterator
from datetime import datetime
import asyncio 
from loguru import logger

from services.exa_client import ExaClient
from services.llm_client import LLMService, LLMProviderName, LLMConfigurationError
from services.report_generator import ReportGenerator
from models.paper import Paper
import config 
import json 

# --- Import UI components and shared data model from the new pages module ---
from pages.main_page import SharedPageData, home_page, render_search_results, render_report_config_form, render_error_message

# Global LLM Service instance
llm_service_instance: Optional[LLMService] = None

# Global Exa Client instance
exa_client_instance: Optional[ExaClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global llm_service_instance
    global exa_client_instance
    logger.info("Application startup: Initializing LLM Service and Exa Client...")
    llm_service_instance = LLMService()
    exa_client_instance = ExaClient(api_key=config.config.EXA_API_KEY)
    try:
        await llm_service_instance.initialize_all_providers()
        logger.info("LLM Service initialized.")
        yield # Application runs here
    except Exception as e:
        logger.exception(f"Critical error during LLM service initialization: {e}")
        yield # Still yield to allow app to run, but LLM will be broken
    finally:
        if llm_service_instance:
            logger.info("Application shutdown: Closing LLM Service...")
            await llm_service_instance.close_all_providers()
            logger.info("LLM Service closed.")
        else:
            logger.info("Application shutdown: LLM Service was not initialized.")

app = FastAPI(lifespan=lifespan)

# --- Route Handlers ---

@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request, search_query: Optional[str] = None) -> HTMLResponse:
    """
    Handles GET requests to the root URL.
    Optionally performs an initial search if 'search_query' is provided.
    Renders the main home page.
    """
    page_data = SharedPageData(current_query=search_query) 
    
    # Perform initial search if query is provided (e.g., from a bookmark or direct URL)
    if search_query and search_query.strip():
        if not config.config.EXA_API_KEY:
            print("Error: EXA_API_KEY not configured for initial search.")
            page_data.search_results = [] 
        else:
            try:
                papers = await asyncio.to_thread(
                    exa_client_instance.search_papers, 
                    query=search_query.strip(), 
                    num_results=config.config.DEFAULT_PAPER_COUNT # Or a specific number for initial load
                )
                page_data.search_results = papers
            except Exception as e:
                print(f"Error during initial Exa API call in get_home: {e}")
                page_data.search_results = [] # Ensure it's an empty list on error
    
    # Generate the full HTML page using the imported home_page component
    component_html = str(home_page(page_data=page_data)) # Uses imported home_page
    return HTMLResponse(content=component_html)


@app.post("/search", response_class=HTMLResponse)
async def handle_search(
    request: Request, 
    search_query: str = FastAPIForm(...), 
    start_published_date: Optional[str] = FastAPIForm(None),
    num_results: Optional[int] = FastAPIForm(config.config.DEFAULT_PAPER_COUNT)
) -> HTMLResponse:
    """
    Handles POST requests from the search form (HTMX).
    Performs a search using the Exa API based on form parameters.
    Returns an HTML fragment of the search results.
    """
    logger.info(f"Received search query: '{search_query}', date filter: {start_published_date}, num_results: {num_results}")
    
    if not config.config.EXA_API_KEY:
        logger.error("Exa API key is not configured for search.")
        error_html = render_error_message(
            "The search service is not configured. Please ensure the API key is set up.",
            context="exa_api_key_missing"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"})

    input_query_stripped = search_query.strip()

    try:
        papers_data: List[Paper] = []
        
        if exa_client_instance.is_url(input_query_stripped):
            logger.info(f"Input '{input_query_stripped}' detected as a URL. Finding similar papers.")
            exa_params_similar = {
                "paper_url": input_query_stripped,
                "num_results": num_results if num_results is not None else config.config.DEFAULT_SIMILAR_PAPER_COUNT, # Using a potentially different default
                "category": "research paper"
            }
            # Potentially add date filters for similar papers if desired, though less common
            papers_data = await asyncio.to_thread(exa_client_instance.find_similar_papers, **exa_params_similar)
            logger.info(f"Found {len(papers_data)} similar papers for URL '{input_query_stripped}'")
        else:
            logger.info(f"Input '{input_query_stripped}' detected as a topic/query. Searching papers.")
            exa_params_search = {
                "query": input_query_stripped,
                "num_results": num_results if num_results is not None else config.config.DEFAULT_PAPER_COUNT,
                "type": "neural", 
                "use_autoprompt": True,
                "category": "research paper" 
            }

            if start_published_date and start_published_date.strip():
                try:
                    datetime.strptime(start_published_date, "%Y-%m-%d") 
                    exa_params_search["start_published_date"] = f"{start_published_date.strip()}T00:00:00Z"
                    logger.info(f"Using date filter for search: {exa_params_search['start_published_date']}")
                except ValueError:
                    logger.warning(f"Invalid start_published_date format received: {start_published_date}. Ignoring date filter.")
                    error_html = render_error_message(
                        f"Invalid date format: '{start_published_date}'. Please use YYYY-MM-DD format.",
                        context="invalid_date_format"
                    ).render()
                    return HTMLResponse(content=error_html, headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"})
            
            papers_data = await asyncio.to_thread(exa_client_instance.search_papers, **exa_params_search)
            logger.info(f"Found {len(papers_data)} papers from Exa for query '{input_query_stripped}'")
        
        results_component = render_search_results(papers_data)
        results_html_fragment = str(results_component)
        
        return HTMLResponse(content=results_html_fragment)
    except Exception as e:
        logger.exception(f"Error during Exa API call in handle_search for query '{input_query_stripped}': {e}")
        error_html = render_error_message(
            "Could not retrieve search results. An unexpected error occurred. Please try again later.",
            context="exa_api_error"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#search-results-container", "HX-Reswap": "innerHTML"})


@app.post("/show-report-config", response_class=HTMLResponse)
async def show_report_config(
    request: Request,
    search_query: str = FastAPIForm(...),
    selected_papers_json: Optional[List[str]] = FastAPIForm(None) 
) -> HTMLResponse:
    """
    Handles POST requests to show the report configuration form.
    It now directly uses the JSON data of selected papers passed from the client.
    """
    if not selected_papers_json:
        logger.warning(f"No papers selected for report configuration from query: '{search_query}'")
        # Render the form but indicate no papers were selected, or show an error/message.
        # For consistency with render_report_config_form's own handling:
        config_form_component = render_report_config_form(
            original_query=search_query, 
            selected_papers_json_list=[], 
            num_selected=0
        )
        return HTMLResponse(content=str(config_form_component))

    deserialized_papers: List[Paper] = []
    try:
        for paper_json_str in selected_papers_json:
            deserialized_papers.append(Paper.model_validate_json(paper_json_str))
        logger.info(f"Successfully deserialized {len(deserialized_papers)} papers for report config.")
    except Exception as e:
        logger.exception(f"Error deserializing paper JSON in show_report_config: {e}")
        error_html = render_error_message(
            "Could not process the selected paper data. Please try selecting papers again.",
            context="paper_deserialization_error_show_config"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"})

    if not deserialized_papers:
        # This case should ideally be caught by the initial check of selected_papers_json_list,
        # but as a safeguard:
        logger.warning(f"Deserialization resulted in no papers for query '{search_query}'")
        error_html = render_error_message(
            "No valid paper data was processed. Please select papers again.",
            context="no_valid_papers_processed"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-config-area", "HX-Reswap": "innerHTML"})
    
  
    logger.info(f"Rendering report config form with {len(deserialized_papers)} papers for query '{search_query}'")
    
    config_form_component = render_report_config_form(
        original_query=search_query,
        selected_papers_json_list=selected_papers_json, # Pass the original JSON strings
        num_selected=len(deserialized_papers)
    )
    return HTMLResponse(content=str(config_form_component))


@app.post("/generate-report", response_class=HTMLResponse)
async def generate_report(
    request: Request,
    original_query_for_report: str = FastAPIForm(...),
    selected_papers_json: List[str] = FastAPIForm(...),
    audience_type: str = FastAPIForm(...),
    summary_size: str = FastAPIForm(...),
    selected_model_key: str = FastAPIForm(...) 
) -> HTMLResponse:
    """
    Handles POST requests to generate the actual report.
    Uses the application-scoped LLMService instance.
    """
    global llm_service_instance
    logger.info(f"Generating report for query: '{original_query_for_report}', audience: {audience_type}, summary size: {summary_size}")
    logger.info(f"Selected Model Key: {selected_model_key}")
    logger.info(f"Number of papers selected for report: {len(selected_papers_json)}")
    
    if llm_service_instance is None or not llm_service_instance._initialized:
        logger.error("LLM Service is not available or not initialized. Cannot generate report.")
        error_html = render_error_message(
            "The report generation service is currently unavailable. Please try again later or contact support.",
            context="llm_service_unavailable"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})

    # Look up model details from the selected key
    model_info = config.config.AVAILABLE_MODELS.get(selected_model_key)
    if not model_info:
        logger.error(f"Invalid selected_model_key: {selected_model_key}. Not found in AVAILABLE_MODELS.")
        error_html = render_error_message(
            f"Invalid model selection: '{selected_model_key}'. Please try again.",
            context="invalid_model_key_error"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})

    actual_model_id = model_info["id"]
    llm_provider_name = model_info["provider"]
    # Extract the input_token_limit from the model_info dictionary
    selected_input_token_limit = model_info["input_token_limit"]

    try:
        deserialized_papers = [Paper.model_validate_json(p_json) for p_json in selected_papers_json]
    except Exception as e:
        logger.exception(f"Error deserializing paper JSON: {e}")
        error_html = render_error_message(
            "Could not process paper data. Please try selecting papers again.",
            context="paper_deserialization_error"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})

    if not deserialized_papers:
        logger.warning("No papers provided for report generation")
        error_html = render_error_message(
            "No papers were provided for report generation. Please select at least one paper.",
            context="no_papers_for_report"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})

    
    # Ensure exa_client_instance is available (initialized in lifespan)
    global exa_client_instance
    if exa_client_instance is None:
        logger.error("Exa Service is not available or not initialized. Cannot generate report.")
        error_html = render_error_message(
            "The paper information service is currently unavailable. Please try again later or contact support.",
            context="exa_service_unavailable"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})

    try:
        # Correctly instantiate ReportGenerator with all required parameters
        report_generator = ReportGenerator(
            llm_service=llm_service_instance,
            exa_client=exa_client_instance, 
            selected_model_api_id=actual_model_id,
            selected_model_provider=llm_provider_name, # type: ignore
            selected_model_input_token_limit=selected_input_token_limit
        )

        # Call generate_report_html without llm_provider and model_name, as ReportGenerator uses its instance attributes
        report_html_content = await report_generator.generate_report_html(
            query_or_url=original_query_for_report, 
            papers_to_summarize=deserialized_papers, 
            audience_type=audience_type,
            summary_size=summary_size
        )
        logger.info(f"Generated report HTML content (first 1000 chars):\n{report_html_content[:1000]}")
        return HTMLResponse(content=report_html_content, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})
    except LLMConfigurationError as e:
        logger.error(f"LLM Configuration Error during report generation: {e}")
        error_html = render_error_message(
            f"Report generation failed due to a configuration issue: {e}. Please check the service setup.",
            context="llm_config_error_report_gen"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})
    except Exception as e:
        logger.exception(f"Unexpected error during report generation: {e}")
        error_html = render_error_message(
            f"An unexpected error occurred while generating the report: {e}. Please try again.",
            context="unexpected_report_gen_error"
        ).render()
        return HTMLResponse(content=error_html, headers={"HX-Retarget": "#report-display-area", "HX-Reswap": "innerHTML"})


# --- Main Execution Block ---

if __name__ == "__main__":
    import uvicorn
    print(f"Starting Uvicorn server on http://{config.config.HOST}:{config.config.PORT}")
    uvicorn.run(
        "app:app", 
        host=config.config.HOST, 
        port=config.config.PORT, 
        reload=config.config.RELOAD_APP,
        log_level="info"
    )