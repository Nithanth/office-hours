"""FastHTML page components for Lux Scholar's main UI."""

import html
from datetime import datetime
from typing import Any, Optional

# FastHTML components used by this page
from fasthtml.components import (
    BUTTON,
    EM,
    FORM,
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
    Textarea,
    Title,
)
from pydantic import BaseModel

import config  # Your project's config module
from models.paper import Paper  # Your Paper Pydantic model
from services.chat.chat_components import ChatBubble, CitationCard


# --- Pydantic Models ---
class SharedPageData(BaseModel):
    """Lightweight container of state used by the home-page template."""

    title: str = config.config.APP_TITLE
    current_query: Optional[str] = None
    search_results: Optional[list[Paper]] = None


# --- UI Components ---
def render_error_message(message: str, context: str = "general") -> Div:
    """Return a styled error banner."""
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
        P(I(f"Generating report for query: '{original_query}'")),
        P(I(f"Based on {num_selected} selected paper(s).")),
        FORM(
            # Hidden fields populated by settings.js so we still POST them
            INPUT(type="hidden", name="audience_type", id="aud-hidden"),
            INPUT(type="hidden", name="selected_model_key", id="model-hidden"),
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
            INPUT(
                type="hidden",
                name="original_query_for_report",
                value=original_query,
                id="original_query_for_report_hidden",
            ),
            *hidden_paper_inputs,
            BUTTON("Generate Report", type="submit", style="margin-right:8px;"),
            hx_post="/generate-report-incremental",
            hx_target="#report-display-area",
            hx_swap="innerHTML",
            hx_indicator="#report-loading-indicator",
            hx_include="[name='selected_papers_json'], #original_query_for_report_hidden, #is_url_input_source_hidden, [name='is_url_input']",
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

    authors_display = "Author(s): N/A"
    if paper.authors:
        if len(paper.authors) > config.config.MAX_AUTHORS_DISPLAY:  # Using config.config
            authors_display = f"Author(s): {', '.join(paper.authors[: config.config.MAX_AUTHORS_DISPLAY])}, et al."
        else:
            authors_display = f"Author(s): {', '.join(paper.authors)}"

    # Basic styling for the paper item
    item_style = "margin-bottom: 1em; padding: 0.5em; border: 1px solid #ccc; position: relative;"
    if is_main_paper:  # Minimal distinction for main paper
        item_style = "margin-bottom: 1em; padding: 0.5em; border: 2px solid var(--main-paper-border); position: relative;"

    # Decide checkbox / hidden input behaviour
    if is_main_paper:
        # Visual checkbox identical to others but user cannot toggle (pointer-events:none)
        selection_inputs = [
            INPUT(
                type="checkbox",
                cls="paper-select-checkbox pinned-checkbox",
                checked=True,
                style="margin-right:5px; pointer-events:none;",
            ),
            INPUT(
                type="hidden",
                name="selected_papers_json",
                value=paper_json_str,
            ),
        ]
        selection_label = Span(
            "⭐",
            cls="pinned-label",
            style="font-size:0.85em;",
        )
    else:
        selection_inputs = [
            INPUT(
                type="checkbox",
                name="selected_papers_json",
                value=paper_json_str,
                cls="paper-select-checkbox",
                style="margin-right: 5px;",
            )
        ]
        selection_label = Span()

    # --------------------------------------------------
    # UX: show ✨ indicator if metadata still incomplete
    # --------------------------------------------------
    needs_enrichment = not paper.authors or not paper.published_date
    enrich_span = (
        Span(" ✨", cls="enrich-indicator", title="Metadata enriching…")
        if needs_enrichment
        else None
    )

    li_attrs = {"style": item_style, "id": f"paper-{paper.id}", "data-paper-id": paper.id}
    if wildcard:
        li_attrs["id"] = "wild-card-item"
        li_attrs["class"] = "wildcard-paper"

    return LI(
        Div(
            *selection_inputs,
            A(
                paper.title if paper.title else "No Title Provided",
                href=paper.url if paper.url else "#",
                target="_blank",
            ),
            enrich_span,
            Br(),
            EM(authors_display),
            Br(),
            Span(f"Published: {formatted_date}"),
            selection_label,
        ),
        **li_attrs,
    )


def render_paper_card(paper: Paper) -> Div:
    """Return CitationCard component for hover popover."""
    return CitationCard(
        title=paper.title or "Untitled",
        authors=paper.authors,
        url=str(paper.url) if paper.url else None,
    )


def render_search_results(
    original_query: str,
    is_url_search: bool,
    papers_list: Optional[list[Paper]] = None,
) -> Div:
    """Render search results list, shuffle/reset controls and hidden HTMX fields."""
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

        # Instead of injecting an inline <script>, update history in a CSP-compliant
        # global JS listener (see static/js/main.js).

        # Pager placeholder – client-side JS populates controls
        results_content.append(Div(id="pager", cls="pager", style="margin-top:10px;"))
        has_results = True
    else:
        pass

    if not has_results:
        pass

    # Add inline size dropdown form and generate button only if there are selectable papers
    if has_results:
        results_content.extend(
            [
                Br(),
                FORM(
                    # Hidden fields populated by settings.js
                    INPUT(type="hidden", name="audience_type", id="aud-hidden"),
                    INPUT(type="hidden", name="selected_model_key", id="model-hidden"),
                    INPUT(
                        type="hidden",
                        name="original_query_for_report",
                        value=original_query,
                        id="original_query_for_report_hidden",
                    ),
                    Br(),
                    Div(
                        Select(
                            Option("Select Report Size", value="", disabled=True, selected=True),
                            Option("S (Concise)", value="S"),
                            Option("M (Standard)", value="M"),
                            Option("L (Detailed)", value="L"),
                            name="summary_size",
                            id="summary_size_select",
                            style="margin-right:0.5rem;",
                        ),
                        BUTTON(
                            "Generate Report",
                            type="submit",
                            id="generate-report-btn",
                            style="margin-left:0.25rem;",
                        ),
                        style="margin-top:0.25rem;",
                    ),
                    hx_post="/generate-report-incremental",
                    hx_target="#report-display-area",
                    hx_swap="innerHTML",
                    hx_indicator="#report-loading-indicator",
                    hx_include="[name='selected_papers_json'], #original_query_for_report_hidden, #is_url_input_source_hidden, [name='is_url_input']",
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
    """Spinner visible while search requests are in-flight."""
    return Div(
        Div(cls="lux-spinner"),
        Span("", id="lux-loading-text", style="display:inline-block; margin-left:4px;"),
        id="loading-indicator",
        cls="htmx-indicator",
    )


def report_loading_indicator_component() -> Div:
    """Spinner shown while the server composes a report."""
    return Div(
        Div(cls="lux-spinner"),
        P("Writing report...", style="display:inline-block;"),
        id="report-loading-indicator",
        cls="htmx-indicator report-specific-indicator",
    )


def ask_loading_indicator_component() -> Div:
    """Spinner shown while waiting for direct LLM answer."""
    return Div(
        Div(cls="lux-spinner"),
        Span("Consulting Assistant...", style="display:inline-block; margin-left:4px;"),
        id="ask-loading-indicator",
        cls="htmx-indicator ask-llm-indicator",
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
            <h4 class="text-lg font-semibold">{html.escape(paper.title)}</h4>
            <p class="text-sm">Source: <a href="{html.escape(paper.url)}" target="_blank" class="hover:underline">{html.escape(paper.url)}</a></p>
            <p class="mt-2">{html.escape(summary)}</p>
            <button class="ask-assistant-btn mt-2 text-sm flex items-center gap-1"
                    hx-get="/paper-modal?paper_id={paper.id}"
                    hx-target="#modal-container"
                    hx-swap="innerHTML">
                <i class="fa-solid fa-comment-dots"></i><span>Ask Assistant About This Paper</span>
            </button>
        </div>"""

    return f"""\
    <div id="report-content" class="mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50">
        <h2 class="text-2xl font-bold mb-4">{html.escape(title)}</h2>
        <div class="main-summary mb-6">
            <h3 class="text-xl font-semibold mb-2">Overall Analysis</h3>
            <p>{html.escape(main_summary)}</p>
        </div>
        <div class="paper-summaries">
            <h3 class="text-xl font-semibold mb-2">Key Paper Summaries</h3>
            {paper_list_html}
        </div>
        <button id="copy-report-btn" class="copy-icon-btn" title="Copy text" aria-label="Copy report" type="button"><i class="fa-solid fa-copy"></i></button>
    </div>"""


def render_chat_modal(paper: Paper, session_id: str) -> Div:
    """Renders the chat modal for a specific paper."""
    paper_id = paper.id
    return Div(
        Div(
            # Header
            Div(
                H2(f"Chat: {paper.title[:60]}..."),
                BUTTON(
                    "×",
                    cls="modal-close-btn",
                    aria_label="Close modal",
                    tabindex="0",
                ),
                cls="modal-header",
            ),
            # Body
            Div(
                Div(
                    id=f"chat-log-{paper_id}",
                    cls="chat-log-container",
                ),
                # This container will receive the SSE connector from the POST response
                Div(id=f"sse-connector-container-{paper_id}"),
                FORM(
                    Textarea(
                        name="message",
                        placeholder="Ask about this paper...",
                        rows="3",
                        style="width:100%;max-width:40rem;height:6rem;font-family:inherit;",
                    ),
                    BUTTON("Send", type="submit", style="margin-top: 5px;"),
                    id=f"chat-form-{paper_id}",
                    hx_post="/chat",
                    hx_target=f"#chat-log-{paper_id}",
                    hx_swap="beforeend",
                ),
                cls="modal-body",
            ),
            cls="modal-content",
        ),
        cls="modal",
    )


def render_chat_message(message: str, role: str) -> Div:
    """Renders a single chat message bubble using ChatBubble component."""
    return ChatBubble(message, role)


def home_page(page_data: SharedPageData) -> Body:
    """The main page layout, including search form and results area."""
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
        results_display_content = ""

    return Body(
        Head(
            Title(page_data.title),
            Script(src="https://cdn.jsdelivr.net/npm/htmx.org@1.9.10/dist/htmx.min.js"),
            # HTMX SSE extension for Server-Sent Events support
            Script(src="https://cdn.jsdelivr.net/npm/htmx.org@1.9.10/dist/ext/sse.js"),
            # Flatpickr date picker CSS & JS (cross-browser calendar support)
            Link(
                rel="stylesheet",
                href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css",
            ),
            Script(src="https://cdn.jsdelivr.net/npm/flatpickr"),
            Link(rel="stylesheet", href="/static/css/spinners.css"),
            # Modular CSS (Phase-3 refactor)
            Link(rel="stylesheet", href="/static/css/base.css"),
            Link(rel="stylesheet", href="/static/css/top-bar.css"),
            Link(rel="stylesheet", href="/static/css/utilities.css"),
            Link(rel="stylesheet", href="/static/css/modal.css"),
            Link(rel="stylesheet", href="/static/css/chat.css"),
            Link(rel="stylesheet", href="/static/css/forms.css"),
            Link(rel="stylesheet", href="/static/css/flatpickr-dark.css"),
            Link(rel="stylesheet", href="/static/css/misc.css"),
            # Font Awesome (self-hosted) – required for icon glyphs
            Link(rel="stylesheet", href="/static/vendor/fontawesome/css/all.min.css"),
            Script(src="/static/js/settings.js"),
            Script(src="/static/js/htmx-url-reset.js", defer=True),
            # Modular JS entry-points
            Script(src="/static/js/main.js", type="module"),
            # No inline init - handled in settings.js
        ),
        # Global toolbar
        Div(
            Link("█▓▒░ Lux Scholar ░▒▓█", href="/", cls="text-lg font-bold mr-4"),
            Div("", style="flex:1"),
            Select(
                Option(
                    "Select Audience",
                    value="",
                    disabled=True,
                    selected=True,
                    # color handled via select.placeholder CSS
                ),
                Option("Highly Technical (PhD/Researcher)", value="technical"),
                Option("Semi-Technical (Lux)", value="semi_technical"),
                Option("Non-technical (Other VCs)", value="non_technical"),
                id="aud-select",
                name="audience",
                style="margin-right:0.5rem;",
            ),
            Select(
                Option(
                    "Select Model",
                    value="",
                    disabled=True,
                    selected=True,
                    # color handled via select.placeholder CSS
                ),
                *[
                    Option(
                        cfg["display_name"],
                        value=key,
                    )
                    for key, cfg in config.config.AVAILABLE_MODELS.items()
                ],
                id="model-select",
                name="model_key",
                style="margin-right:0.5rem;",
            ),
            BUTTON(
                "🌙 Dark",
                id="lux-dark-toggle",
                aria_label="Toggle dark mode",
                type="button",
                style="padding:1px 4px;margin-left:4px;",
            ),
            cls="top-bar",
            style="display:flex;align-items:center;padding:4px 8px;",
        ),
        Div(id="status-banner"),  # Placeholder for busy/alert banners
        Div(
            P(I("Enter a research topic, query, or paper link:")),
            FORM(  # Search Form
                Label(for_="search_query"),
                INPUT(
                    type="search",
                    id="search_query",
                    name="search_query",
                    required=True,
                    placeholder="e.g., 'transformer architecture overview', or 'https://www.arxiv.org/pdf/2506.08388'",
                    value=page_data.current_query or "",
                    style="width:40rem;max-width:100%;",
                ),
                Br(),
                # Author input removed per new requirements
                Br(),
                Div(
                    Label(
                        "Published After ",
                        EM("(optional)", style="color:#666; font-style:italic;"),
                        ":",
                        for_="start_published_date",
                    ),
                    INPUT(
                        type="date",
                        id="start_published_date",
                        name="start_published_date",
                        placeholder="YYYY-MM-DD",
                        style="margin-left:6px;",
                    ),
                    style="display:flex; align-items:center; gap:6px; margin-bottom:6px;",
                ),
                Div(
                    Div(
                        Label(
                            "Source Domain ",
                            EM("(optional)", style="color:#666; font-style:italic;"),
                            ":",
                            for_="include_domains",
                        ),
                        Span(
                            "Cmd/Ctrl to select/deselect multiple",
                            style="font-size:0.75em; color:#666; margin-top:4px;",
                        ),
                        BUTTON(
                            "Clear",
                            type="button",
                            id="clear-domains-btn",
                            style="margin-top:6px; width:48px;",
                        ),
                        style="display:flex; flex-direction:column; gap:4px; justify-content:flex-start;",
                    ),
                    Select(
                        Option("arxiv.org", value="arxiv.org"),
                        Option("biorxiv.org", value="biorxiv.org"),
                        Option("medrxiv.org", value="medrxiv.org"),
                        Option("ieeexplore.ieee.org", value="ieeexplore.ieee.org"),
                        Option("dl.acm.org", value="dl.acm.org"),
                        Option("openreview.net", value="openreview.net"),
                        Option("aclanthology.org", value="aclanthology.org"),
                        Option("aclweb.org", value="aclweb.org"),
                        id="include_domains",
                        name="include_domains",
                        multiple=True,
                        size="4",
                        style="width:220px; min-height:88px; margin-left:8px;",
                    ),
                    style="display:flex; align-items:flex-start; gap:8px; margin-bottom:6px;",
                ),
                # Number of Results input removed – page size handled internally.
                Br(),
                # Buttons choose workflow (paper search)
                BUTTON(
                    "Search Papers",
                    type="submit",
                    name="mode",
                    value="auto",
                    onclick="document.getElementById('lux-loading-text').textContent='Searching the PaperVerse...'",
                ),
                Br(),
                loading_indicator_component(),
                hx_post="/search",
                hx_target="#search-results-container",
                hx_swap="innerHTML",
                hx_indicator="#loading-indicator",
                hx_push_url="false",
                **{"hx-indicator": "#loading-indicator"},
                onsubmit="document.getElementById('report-display-area').innerHTML='';document.getElementById('report-config-area').innerHTML='';document.getElementById('ask-result-container').innerHTML='';",
                id="paper-search-form",
                style="background:inherit;padding:0.5rem 0;",
            ),
            # Results and Report Form (wraps hidden inputs etc.)
            FORM(
                # Search results are displayed here, targeted by the search form's hx_target
                Div(
                    results_display_content,
                    id="search-results-container",
                ),  # Corrected: child directly passed
                # Report configuration form will be loaded here
                Div(id="report-config-area"),
                report_loading_indicator_component(),  # For when report is generating
                # Actual report will be displayed here
                Div(id="report-display-area"),
                id="results-and-report-form",
            ),
            # ---------------- Ask LLM block (standalone) ----------------
            Br(),
            P(
                I("Research assistant for independent or follow-up questions:"),
                style="margin-top:0.75rem;font-size:0.9rem;",
                _class="ask-llm-subtitle",
            ),
            FORM(
                Textarea(
                    id="direct_question",
                    name="question",
                    placeholder="e.g., 'Summarize the transformer architecture'",
                    style="width:100%;max-width:40rem;height:6rem;font-family:inherit;",
                ),
                Br(),
                BUTTON(
                    "Ask Assistant",
                    type="submit",
                    onclick="document.getElementById('lux-loading-text').textContent='Consulting Assistant...'",
                ),
                hx_post="/ask-llm",
                hx_target="#ask-result-container",
                hx_swap="innerHTML",
                hx_indicator="#ask-loading-indicator",
                hx_include="#model-select,#aud-select",
                onsubmit="document.getElementById('ask-result-container').innerHTML='';",
                id="ask-assistant-form",
                style="padding:0.5rem 0;",
            ),
            ask_loading_indicator_component(),
            Div(id="ask-result-container"),
            Div(id="modal-container"),
            cls="container",
        ),
        Footer(
            P(f"  {config.config.YEAR} {config.config.APP_TITLE}. v{config.config.VERSION}."),
            style="margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #ccc; text-align:center; font-size:0.8rem; color:#666;",
        ),
        **{"hx-push-url": "false"},
    )  # Close Body
