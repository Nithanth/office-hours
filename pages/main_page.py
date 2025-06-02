from typing import List, Optional
from pydantic import BaseModel

# Ensure all FastHTML components used by your page and sub-components are imported
from fasthtml.components import (
    Title, P, H1, H2, Div, FORM, INPUT, BUTTON, UL, LI, A, Link, Head, Body, 
    Script, HR, Main, Footer, HTML, Style, Label, Br, Select, Option
)

import config # Your project's config module
from models.paper import Paper # Your Paper Pydantic model

# --- Pydantic Models ---
class SharedPageData(BaseModel):
    title: str = config.config.APP_TITLE 
    current_query: Optional[str] = None
    search_results: Optional[List[Paper]] = None

# --- UI Components ---

def render_error_message(message: str, context: str = "general") -> Div:
    """
    Renders a user-friendly error message with consistent styling.
    
    Args:
        message: The error message to display to the user
        context: Optional context identifier for the error (useful for styling or analytics)
    
    Returns:
        A Div component containing the formatted error message
    """
    return Div(
        P(message),
        cls=f"error-message error-context-{context}",
        style="color: #d9534f; background-color: #f9f2f2; border: 1px solid #d9534f; padding: 10px; border-radius: 4px; margin: 10px 0;"
    )

def render_report_config_form(original_query: str, selected_papers_json_list: List[str], num_selected: int) -> Div:
    """Renders the form for configuring report parameters."""
    if num_selected == 0:
        return Div(P("No papers selected. Please select papers from the search results to generate a report."))

    hidden_paper_inputs = []
    for paper_json_str in selected_papers_json_list:
        hidden_paper_inputs.append(INPUT(type="hidden", name="selected_papers_json", value=paper_json_str))

    return Div(
        H2("Configure Report Generation"),
        P(f"Generating report for query: '{original_query}'"),
        P(f"Based on {num_selected} selected paper(s)."),
        FORM(
            Label("Select Target Audience:", for_="audience_type"), Br(),
            Select(
                Option("Highly Technical (PhD/Researcher)", value="technical"),
                Option("Semi-technical (Lux)", value="semi_technical", selected=True),
                Option("Non-Technical (Other VCs) ", value="non-technical"),
                name="audience_type", id="audience_type_select"
            ), Br(), Br(),
            Label("Select Model:", for_="model_select"), Br(),
            Select(
                *[Option(model_config['display_name'],
                         value=model_key,
                         selected=(model_key == config.config.DEFAULT_MODEL_KEY))
                  for model_key, model_config in config.config.AVAILABLE_MODELS.items()],
                name="selected_model_key", id="model_select"
            ), Br(), Br(),
            Label("Select Report Size:", for_="summary_size_select"), Br(),
            Select(
                Option("S (Concise)", value="S"),
                Option("M (Standard)", value="M", selected=True),
                Option("L (Detailed)", value="L"),
                Option("XL (Comprehensive)", value="XL"),
                name="summary_size", id="summary_size_select"
            ), Br(), Br(),
            INPUT(type="hidden", name="original_query_for_report", value=original_query),
            *hidden_paper_inputs, 
            BUTTON("Generate Report", type="submit"),
            hx_post="/generate-report",
            hx_target="#report-display-area",
            hx_swap="innerHTML",
            hx_indicator="#report-loading-indicator"
        )
    )
def render_search_results(papers: Optional[List[Paper]]) -> Div:
    """
    Renders a list of research papers with improved UI presentation.
    
    Improvements:
    - Paper titles are hyperlinked to their source URLs
    - Publication dates are formatted for better readability
    - Paper IDs are hidden
    - Scores are de-emphasized
    - Checkboxes now carry the full paper JSON data.
    
    Args:
        papers: List of Paper objects to display, or None if no results
        
    Returns:
        A Div component containing the formatted search results
    """
    if not papers:
        return Div(
            P("No search results to display."),
            cls="no-results-message",
            style="padding: 15px; text-align: center; color: #666;"
        )
    
    list_items = []
    for paper in papers:
        formatted_date = "Unknown date"
        if paper.published_date:
            try:
                date_parts = paper.published_date.split('-')
                if len(date_parts) >= 3:
                    year, month, day = date_parts[0], date_parts[1], date_parts[2].split('T')[0]
                    months = ["January", "February", "March", "April", "May", "June", 
                              "July", "August", "September", "October", "November", "December"]
                    try:
                        month_name = months[int(month) - 1]
                        formatted_date = f"{month_name} {int(day)}, {year}"
                    except (ValueError, IndexError):
                        formatted_date = paper.published_date
                else:
                    formatted_date = paper.published_date
            except Exception:
                formatted_date = paper.published_date
        
        paper_json_str = paper.model_dump_json() 
        list_items.append(
            LI(
                Div(
                    INPUT(
                        type="checkbox", 
                        name="selected_papers_json",  
                        value=paper_json_str,       
                        cls="paper-select-checkbox", 
                        style="margin-right: 10px; transform: scale(1.2);"
                    ),
                    A(
                        paper.title, 
                        href=paper.url, 
                        target="_blank",
                        style="font-weight: bold; color: #2c5282; text-decoration: none; font-size: 1.1em; display: block; margin: 5px 0;"
                    ),
                    P(
                        f"Authors: {', '.join(paper.authors) if paper.authors else 'Unknown'}", 
                        style="margin: 5px 0; color: #4a5568;"
                    ),
                    P(
                        f"Published: {formatted_date}", 
                        style="margin: 5px 0; color: #718096; font-size: 0.9em;"
                    ),
                    style="padding: 10px; border: 1px solid #E2E8F0; border-radius: 5px; margin-bottom: 15px;"
                ),
                style="list-style: none; margin-bottom: 15px;"
            )
        )
    
    return Div(
        H2("Retrieved Papers", style="color: #2D3748; margin-bottom: 15px;"),
        UL(
            *list_items,
            style="padding: 0; margin: 0;"
        ) if list_items else P("No papers found.", style="text-align: center; color: #666;")
    )

def loading_indicator_component() -> Div:
    return Div(P("Searching the PaperVerse..."), id="loading-indicator", cls="htmx-indicator")

def report_loading_indicator_component() -> Div:
    return Div(
        P("Writing report... This may take a minute or two, please wait!"), 
        id="report-loading-indicator", 
        cls="htmx-indicator report-specific-indicator", 
        style="padding: 10px; background-color: #e6f7ff; border: 1px solid #91d5ff; color: #0050b3; border-radius: 4px; margin-top:15px; text-align: center;"
    )

def home_page(page_data: SharedPageData) -> Div:
    """Main page layout with search form and results area."""
    indicator_css = """
      .htmx-indicator { /* General class for all indicators */
        display: none; /* Hidden by default */
        opacity: 0;
        transition: opacity 200ms ease-in;
      }
      .htmx-request .htmx-indicator, /* Show any indicator during request */
      .htmx-request.htmx-indicator {
        display: inline-block; /* Changed for better layout control */
        opacity: 1;
      }
      /* Specific styling for the report indicator if needed, beyond the inline style */
      .report-specific-indicator { 
        /* You can add more specific styles here if the inline ones are not enough */
        /* For example, ensure it takes full width if desired: width: 100%; */
      }
    """
    search_form = FORM(
        Label("Query:", for_="search_query"),
        INPUT(type="search", id="search_query", name="search_query", required=True, placeholder="e.g., 'transformer architecture overview', 'applications of AI in drug discovery', or paste an arXiv URL", value=page_data.current_query or "", style="width: 70%; padding: 8px; margin-bottom: 10px;"),
        Br(),
        Label("Published After:", for_="start_published_date"), 
        INPUT(type="date", id="start_published_date", name="start_published_date"),
        Br(),
        Label("Number of Results:", for_="num_results"),
        INPUT(type="number", id="num_results", name="num_results", min="1", max=str(config.config.MAX_PAPERS_PER_SEARCH), value=str(config.config.DEFAULT_PAPER_COUNT)),
        Br(),
        BUTTON("Search", type="submit"),
        hx_post="/search",
        hx_target="#search-results-container",
        hx_swap="innerHTML",
        hx_indicator="#loading-indicator" 
    )
    
    results_display_content = render_search_results(page_data.search_results) if page_data.search_results else P("Search results will appear here.")

    return Div(
        Head(
            Title(page_data.title),
            Script(src="https://unpkg.com/htmx.org@1.9.10"), 
            Style(indicator_css) 
        ),
        Body(
            H1(page_data.title),
            P("Enter a research topic or query."),
            search_form,
            loading_indicator_component(), 
            FORM(
                Div(id="search-results-container", content=[results_display_content]),
                Br(),
                BUTTON(
                    "Configure Report", 
                    id="create-report-button",
                    hx_post="/show-report-config",
                    hx_target="#report-config-area",
                    hx_swap="innerHTML",
                    hx_include="[name='selected_papers_json']:checked, [name='search_query']", 
                    hx_indicator="#loading-indicator"
                ),
                Div(id="report-config-area"), 
                Br(),
                report_loading_indicator_component(),
                Div(id="report-display-area"),  
                id="results-and-report-form"
            )
        )
    )