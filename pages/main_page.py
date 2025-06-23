from datetime import datetime
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
    I,
    Label,
    Link,
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


def _render_paper_item(paper: Paper, is_main_paper: bool = False, wildcard: bool = False) -> LI:
    """Helper function to render a single paper item for the search results list."""
    formatted_date = "Unknown date"
    if paper.published_date:
        try:
            # Normalise to string first for parsing convenience
            date_iso = (
                paper.published_date.isoformat()
                if isinstance(paper.published_date, datetime)
                else str(paper.published_date)
            )
            date_parts = date_iso.split("-")
            if len(date_parts) >= 3:
                year, month, day_part = date_parts[0], date_parts[1], date_parts[2]
                day = day_part.split("T")[0]  # Remove any time component
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
                month_idx = int(month) - 1
                if 0 <= month_idx < len(months):
                    formatted_date = f"{months[month_idx]} {int(day)}, {year}"
                else:
                    formatted_date = date_iso
            else:
                formatted_date = date_iso
        except Exception:
            formatted_date = str(paper.published_date)

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
            "📌 pinned", style="font-size: 0.85em; margin-left: 6px; color:#555;"
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

    li_attrs = {"style": item_style}
    if wildcard:
        li_attrs["id"] = "wild-card-item"
        li_attrs["class"] = "wildcard-paper"

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
        ),
        **li_attrs,
    )


def render_search_results(
    original_query: str,
    is_url_search: bool,
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

    # Unified list display for both query and URL searches
    if papers_list:
        list_items = [
            _render_paper_item(
                p,
                is_main_paper=(is_url_search and i == 0),
                wildcard=(i == len(papers_list) - 1),
            )
            for i, p in enumerate(papers_list)
        ]
        # Main list that HTMX endpoints will target
        results_content.append(
            UL(
                *list_items,
                id="papers-list",
                style="list-style-type: none; padding-left: 0;",
            )
        )

        # Shuffle / wildcard / reset controls (work for both URL & query searches)
        results_content.append(
            Div(
                BUTTON(
                    "🎲 Shuffle random pick",
                    id="shuffle-btn",
                    hx_post="/shuffle",
                    hx_target="#wild-card-item",
                    hx_swap="outerHTML",
                ),
                BUTTON(
                    "🔀 Shuffle all",
                    id="shuffle-all-btn",
                    hx_post="/shuffle_all",
                    hx_target="#papers-list",
                    hx_swap="outerHTML",
                    style="margin-left:0.5rem;",
                ),
                BUTTON(
                    "↩️ Reset order",
                    id="reset-btn",
                    hx_post="/reset_results",
                    hx_target="#papers-list",
                    hx_swap="outerHTML",
                    style="margin-left:0.5rem;",
                ),
                style="margin-top:10px;",
            )
        )
        has_results = True
    else:
        results_content.append(P("No papers found for the given search."))

    if not has_results:
        results_content.append(P("No search results to display."))

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


def render_report(
    title: str, main_summary: str, paper_summaries: list[str], papers: list[Paper]
) -> str:
    """Builds the final HTML for the report."""
    paper_list_html = ""
    # Ensure we only iterate as many times as we have summaries or papers
    num_items = min(len(paper_summaries), len(papers))

    for i in range(num_items):
        paper = papers[i]
        summary = paper_summaries[i]
        paper_list_html += f"""\
        <div class="paper-summary-item mt-4 pt-4 border-t border-gray-700">
            <h4 class="text-lg font-semibold">{paper.title}</h4>
            <p class="text-sm">Source: <a href="{paper.url}" target="_blank" class="hover:underline">{paper.url}</a></p>
            <p class="mt-2">{summary}</p>
        </div>"""

    return f"""\
    <div id="report-content" class="mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50">
        <h2 class="text-2xl font-bold mb-4">{title}</h2>
        <div class="main-summary mb-6">
            <h3 class="text-xl font-semibold mb-2">Overall Analysis</h3>
            <p>{main_summary}</p>
        </div>
        <div class="paper-summaries">
            <h3 class="text-xl font-semibold mb-2">Key Paper Summaries</h3>
            {paper_list_html}
        </div>
    </div>"""


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
        results_display_content = P(I("Search results will appear here."))

    return Div(  # Main page content wrapper
        Head(
            Title(page_data.title),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            # Flatpickr date picker CSS & JS (cross-browser calendar support)
            Link(
                rel="stylesheet",
                href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css",
            ),
            Script(src="https://cdn.jsdelivr.net/npm/flatpickr"),
            Script(
                "document.addEventListener('DOMContentLoaded', function() {\n"
                "  if (window.flatpickr) {\n"
                "    flatpickr('#start_published_date', {dateFormat: 'Y-m-d'});\n"
                "  }\n"
                "});"
            ),
            Style(indicator_css),
            Link(rel="stylesheet", href="/static/css/styles.css"),
            # Re-add small script to handle manual dark-mode toggle without inline style overrides
            Script(src="/static/js/app.js"),  # External JS handles dark-mode toggle
        ),
        Body(
            Div(
                BUTTON("🌙 Dark Mode", id="lux-dark-toggle", type="button"),
                style="text-align:right; margin-bottom:0.5rem;",
            ),
            H1(page_data.title),
            P("Enter a research topic, query, or paper link:"),
            FORM(  # Search Form
                Label(for_="search_query"),
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
                Label("Published After:", for_="start_published_date"),
                INPUT(
                    type="date",
                    id="start_published_date",
                    name="start_published_date",
                    placeholder="YYYY-MM-DD",
                ),
                Br(),
                Label("Source Domain:", for_="domain_filter"),
                Select(
                    Option("Any", value="any"),
                    Option("arxiv.org", value="arxiv.org"),
                    Option("openreview.net", value="openreview.net"),
                    Option("aclweb.org", value="aclweb.org"),
                    id="domain_filter",
                    name="domain_filter",
                ),
                Br(),
                Label("Number of Results:", for_="num_results"),
                INPUT(
                    type="number",
                    id="num_results",
                    name="num_results",
                    min="1",
                    max="10",
                    value=str(config.config.DEFAULT_PAPER_COUNT),
                ),
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
                P(f"  {config.config.YEAR} {config.config.APP_TITLE}. v{config.config.VERSION}."),
                style="margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #ccc;",
            ),
        ),
    )
