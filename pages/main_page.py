from typing import Any, Optional

# Ensure all FastHTML components used by your page and sub-components are imported
from fasthtml.components import (
    BUTTON,
    EM,
    FORM,
    H1,
    H2,
    INPUT,
    LI,
    UL,
    A,
    Body,
    Br,
    Div,
    Footer,
    Head,
    Label,
    Option,
    P,
    Script,
    Select,
    Span,
    Style,
    Title,
)
from pydantic import BaseModel

import config  # Your project's config module
from models.paper import Paper  # Your Paper Pydantic model


# --- Pydantic Models ---
class SharedPageData(BaseModel):
    title: str = config.config.APP_TITLE
    current_query: Optional[str] = None
    search_results: Optional[list[Paper]] = None


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
        style="color: #d9534f; background-color: #f9f2f2; border: 1px solid #d9534f; padding: 10px; border-radius: 4px; margin: 10px 0;",
    )


def render_report_config_form(
    original_query: str, selected_papers_json_list: list[str], num_selected: int, is_url_input: bool
) -> Div:
    """Renders the form for configuring report parameters."""
    if num_selected == 0:
        return Div(
            P(
                "No papers selected. Please select papers from the search results to generate a report."
            )
        )

    hidden_paper_inputs = []
    for paper_json_str in selected_papers_json_list:
        hidden_paper_inputs.append(
            INPUT(type="hidden", name="selected_papers_json", value=paper_json_str)
        )

    # Add hidden input for is_url_input
    hidden_paper_inputs.append(
        INPUT(type="hidden", name="is_url_input", value=str(is_url_input).lower())
    )

    return Div(
        H2("Configure Report Generation"),
        P(f"Generating report for query: '{original_query}'"),
        P(f"Based on {num_selected} selected paper(s)."),
        FORM(
            Label("Select Target Audience:", for_="audience_type"),
            Br(),
            Select(
                Option("Highly Technical (PhD/Researcher)", value="technical"),
                Option("Semi-technical (Lux)", value="semi_technical", selected=True),
                Option("Non-Technical (Other VCs) ", value="non-technical"),
                name="audience_type",
                id="audience_type_select",
            ),
            Br(),
            Br(),
            Label("Select Model:", for_="model_select"),
            Br(),
            Select(
                *[
                    Option(
                        model_config["display_name"],
                        value=model_key,
                        selected=(model_key == config.config.DEFAULT_MODEL_KEY),
                    )
                    for model_key, model_config in config.config.AVAILABLE_MODELS.items()
                ],
                name="selected_model_key",
                id="model_select",
            ),
            Br(),
            Br(),
            Label("Select Report Size:", for_="summary_size_select"),
            Br(),
            Select(
                Option("S (Concise)", value="S"),
                Option("M (Standard)", value="M", selected=True),
                Option("L (Detailed)", value="L"),
                Option("XL (Comprehensive)", value="XL"),
                name="summary_size",
                id="summary_size_select",
            ),
            Br(),
            Br(),
            INPUT(type="hidden", name="original_query_for_report", value=original_query),
            *hidden_paper_inputs,
            BUTTON("Generate Report", type="submit"),
            hx_post="/generate-report",
            hx_target="#report-display-area",
            hx_swap="innerHTML",
            hx_indicator="#report-loading-indicator",
        ),
    )


def _render_paper_item(paper: Paper, is_main_paper: bool = False) -> LI:
    """Helper function to render a single paper item for the search results list."""
    formatted_date = "Unknown date"
    if paper.published_date:
        try:
            date_parts = paper.published_date.split("-")
            if len(date_parts) >= 3:
                year, month, day = date_parts[0], date_parts[1], date_parts[2].split("T")[0]
                months = [
                    "January",
                    "February",
                    "March",
                    "April",
                    "May",
                    "June",
                    "July",
                    "August",
                    "September",
                    "October",
                    "November",
                    "December",
                ]
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

    authors_display = "N/A"
    if paper.authors:
        if len(paper.authors) > config.config.MAX_AUTHORS_DISPLAY:  # Using config.config
            authors_display = (
                f"{', '.join(paper.authors[: config.config.MAX_AUTHORS_DISPLAY])}, et al."
            )
        else:
            authors_display = ", ".join(paper.authors)

    # Basic styling for the paper item
    item_style = "margin-bottom: 1em; padding: 0.5em; border: 1px solid #ccc;"
    if is_main_paper:  # Minimal distinction for main paper
        item_style = "margin-bottom: 1em; padding: 0.5em; border: 2px solid #007bff;"

    # Decide checkbox / hidden input behaviour
    if is_main_paper:
        selection_input = INPUT(
            type="hidden",  # always submitted, not user-toggleable
            name="selected_papers_json",
            value=paper_json_str,
        )
        selection_label = Span(
            "(Input paper – always included)", style="font-size: 0.9em; margin-left: 6px;"
        )
    else:
        selection_input = INPUT(
            type="checkbox",
            name="selected_papers_json",
            value=paper_json_str,
            cls="paper-select-checkbox",
            style="margin-right: 5px;",
        )
        selection_label = Span()

    return LI(
        Div(
            selection_input,
            A(
                paper.title if paper.title else "No Title Provided",
                href=paper.url if paper.url else "#",
                target="_blank",
            ),
            P(EM(authors_display)),
            P(f"Published: {formatted_date}"),
            # ID hidden by default; uncomment for debug
            # P(f"ID: {paper.id[:20]}..." if paper.id else "No ID"),
            selection_label,
            style=item_style,
        )
    )


def render_search_results(
    original_query: str,
    is_url_search: bool,
    main_paper: Optional[Paper] = None,
    similar_papers: Optional[list[Paper]] = None,
    papers_list: Optional[list[Paper]] = None,
) -> Div:
    # Out-of-band clears so a new search hides any old report UI instantly
    clearing_divs = [
        Div(id="report-config-area", **{"hx-swap-oob": "true"}),
        Div(id="report-display-area", **{"hx-swap-oob": "true"}),
    ]

    results_content: list[Any] = []  # mix of components
    has_results = False

    # Hidden fields to carry forward context for report generation
    hidden_input_fields = [
        INPUT(
            type="hidden",
            id="original_query_for_report_hidden",
            name="original_search_query_for_report",
            value=original_query,
        ),
        INPUT(
            type="hidden",
            id="is_url_input_source_hidden",
            name="is_url_input_source",
            value="true" if is_url_search else "false",
        ),
    ]

    if is_url_search:
        if main_paper:
            results_content.append(H2("Input Paper Details"))
            results_content.append(
                UL(
                    _render_paper_item(main_paper, is_main_paper=True),
                    style="list-style-type: none; padding-left: 0;",
                )
            )
            has_results = True
        else:
            results_content.append(P("Could not retrieve details for the input URL."))

        # Helper to normalize arXiv URLs to bare id (e.g., 2006.09661)
        def _arxiv_id_from_url(u: str | None) -> str | None:
            if not u:
                return None
            u_str = str(u)
            if "arxiv.org" not in u_str:
                return None
            # Strip scheme and domain
            parts = u_str.split("arxiv.org/")[-1]
            parts = parts.replace("pdf/", "").replace("abs/", "")
            parts = parts.replace(".pdf", "")
            # Remove version suffix like v1
            parts = parts.split("v")[0]
            return parts.strip("/")

        if similar_papers and main_paper:
            # Cast to str to satisfy MyPy since .url may be Pydantic HttpUrl
            main_id = _arxiv_id_from_url(str(main_paper.url) if main_paper.url else None)
            cleaned_similar: list[Paper] = []
            enriched_main: Paper | None = None
            for p in similar_papers:
                # Ensure p.url is cast to str for type checking
                candidate_id = _arxiv_id_from_url(str(p.url) if p.url else None)
                if p.url == main_paper.url or (main_id and candidate_id == main_id):
                    # Potential duplicate with richer metadata
                    enriched_main = p
                    continue
                cleaned_similar.append(p)

            # If main_paper lacks a real title or authors, replace with enriched version
            if enriched_main and (
                (not main_paper.title or main_paper.title == "No Title Provided")
                or not main_paper.authors
                or not main_paper.published_date
            ):
                if not main_paper.title or main_paper.title == "No Title Provided":
                    main_paper.title = enriched_main.title
                if (not main_paper.authors) and enriched_main.authors:
                    main_paper.authors = enriched_main.authors
                if (not main_paper.published_date) and enriched_main.published_date:
                    main_paper.published_date = enriched_main.published_date

            similar_papers = cleaned_similar

        if similar_papers:
            results_content.append(H2("Similar Papers Found"))
            similar_list_items = [_render_paper_item(p) for p in similar_papers]
            results_content.append(
                UL(*similar_list_items, style="list-style-type: none; padding-left: 0;")
            )
            has_results = True
        elif main_paper:
            results_content.append(P("No similar papers found for the input URL."))

        if not main_paper and not similar_papers:
            results_content.append(P("No papers found for the input URL or any similar ones."))

    else:  # General topic search
        if papers_list:
            results_content.append(H2("Retrieved Papers"))
            general_list_items = [_render_paper_item(p) for p in papers_list]
            results_content.append(
                UL(*general_list_items, style="list-style-type: none; padding-left: 0;")
            )
            has_results = True
        else:
            results_content.append(P("No papers found for your query."))

    if not has_results and not results_content:
        # If truly no results from any category for a URL search, or no results for topic.
        # The specific messages above handle most cases. This is a fallback.
        results_content.append(P("No information to display for your query."))

    # Add Configure Report button only if there are selectable papers
    if has_results:
        results_content.extend(
            [
                Br(),
                BUTTON(
                    "Configure Report",
                    id="create-report-button",
                    hx_post="/show-report-config",
                    hx_target="#report-config-area",
                    hx_swap="innerHTML",
                    hx_include="[name='selected_papers_json']:checked, [name='search_query'], [name='is_url_input_source']",
                    hx_indicator="#loading-indicator",
                ),
            ]
        )

    return Div(
        *clearing_divs,  # first ensure old report UI cleared
        *hidden_input_fields,
        *results_content,
        # The ID "search-results-content" was on the Div returned by the *newer* render_search_results.
        # The old home_page.py targets "#search-results-container" for hx_target.
        # The old home_page.py targets "#search-results-container" for hx-target.
        # So this returned Div doesn't need an ID unless specifically targeted by something else.
    )


def loading_indicator_component() -> Div:
    return Div(
        Div(cls="lux-spinner"),
        Span("", id="lux-loading-text", style="display:inline-block; margin-left:4px;"),
        id="loading-indicator",
        cls="htmx-indicator",
    )


def report_loading_indicator_component() -> Div:
    return Div(
        Div(cls="lux-spinner"),
        P("Writing report...", style="display:inline-block;"),
        id="report-loading-indicator",
        cls="htmx-indicator report-specific-indicator",
    )


def home_page(page_data: SharedPageData) -> Div:
    """Main page layout with search form and results area."""
    indicator_css = """
      .htmx-indicator { display: none; opacity: 0; transition: opacity 200ms ease-in; }
      .htmx-request .htmx-indicator, .htmx-request.htmx-indicator { display: inline-block; opacity: 1; }

      /* Simple spinner */
      @keyframes lux-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg);} }
      .lux-spinner { border: 4px solid #ccc; border-top: 4px solid #007bff; border-radius: 50%; width: 24px; height: 24px; animation: lux-spin 1s linear infinite; display:inline-block; margin-right:8px; vertical-align: middle; }
    """

    # Initial content for search results area
    if (
        page_data.search_results is not None
    ):  # Check if search_results is explicitly set (even if empty)
        # This assumes render_search_results can handle an empty list for page_data.search_results
        # and that current_query and is_url_search context would be available or handled.
        # For initial load, these aren't available, so we pass default/empty values.
        results_display_content = render_search_results(
            original_query=page_data.current_query or "",
            is_url_search=False,  # Assume not a URL search on initial load
            papers_list=page_data.search_results,
        )
    else:  # No search performed yet, or search_results is None
        results_display_content = P("Search results will appear here.")

    return Div(  # This is the main Div wrapper for the entire page content
        Head(
            Title(page_data.title),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Style(indicator_css),
            # Minimal dark-mode CSS (keeps retro look)
            Style(
                """
                body.lux-dark { background-color:#1a1a1a; color:#e4e4e4; }
                body.lux-dark a { color:#a8cfff; }
                .lux-dark input, .lux-dark select, .lux-dark textarea { background:#292929; color:#e4e4e4; border-color:#666; }
                .lux-dark button { background:#333; color:#e4e4e4; border-color:#888; }
                """,
            ),
        ),
        Body(
            Div(
                BUTTON(
                    "Toggle Dark Mode",
                    type="button",
                    onclick="document.body.classList.toggle('lux-dark')",
                ),
                style="text-align:right; margin-bottom:0.5rem;",
            ),
            H1(page_data.title),
            P("Enter a research topic or query."),
            FORM(  # Search Form
                Label("Query:", for_="search_query"),
                INPUT(
                    type="search",
                    id="search_query",
                    name="search_query",
                    required=True,
                    placeholder="e.g., 'transformer architecture overview', or paste an arXiv URL",
                    value=page_data.current_query or "",
                    style="width:40rem;max-width:90%;",
                ),
                Br(),
                # Author input removed per new requirements
                Br(),
                # Buttons choose workflow
                BUTTON(
                    "Search Papers",
                    type="submit",
                    name="mode",
                    value="auto",
                    onclick="document.getElementById('lux-loading-text').textContent='Searching the PaperVerse...'",
                ),
                BUTTON(
                    "Ask LLM",
                    type="submit",
                    name="mode",
                    value="direct",
                    onclick="document.getElementById('lux-loading-text').textContent='Consulting LLM...'",
                    style="margin-left:0.5rem;",
                ),
                Br(),
                Label("Published After:", for_="start_published_date"),
                INPUT(type="date", id="start_published_date", name="start_published_date"),
                Br(),
                Label("Number of Results:", for_="num_results"),
                INPUT(
                    type="number",
                    id="num_results",
                    name="num_results",
                    min="1",
                    max=str(config.config.MAX_PAPERS_PER_SEARCH),
                    value=str(config.config.DEFAULT_PAPER_COUNT),
                ),
                Br(),
                hx_post="/search",
                hx_target="#search-results-container",  # Targets the Div below
                hx_swap="innerHTML",
                hx_indicator="#loading-indicator",
                style="background:inherit;padding:0.5rem 0;border-bottom:1px solid #ccc;",
            ),
            loading_indicator_component(),
            # This outer FORM for report configuration might be what you intended
            FORM(
                # Search results are displayed here, targeted by the search form's hx_target
                Div(
                    results_display_content,
                    id="search-results-container",
                ),  # Corrected: child directly passed
                Br(),
                # Report configuration form will be loaded here
                Div(id="report-config-area"),
                Br(),
                report_loading_indicator_component(),  # For when report is generating
                # Actual report will be displayed here
                Div(id="report-display-area"),
                id="results-and-report-form",  # ID for this form container
            ),
            Footer(
                P(f"  {config.config.YEAR} {config.config.APP_TITLE}."),
                style="margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #ccc;",
            ),
        ),
    )
