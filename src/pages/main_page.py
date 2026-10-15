"""FastHTML page components for Office Hours' main UI."""

import html
import re
import urllib.parse
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
    Details,
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
    Summary,
    Textarea,
    Title,
)
from loguru import logger
from pydantic import BaseModel

import config  # Your project's config module
from models.paper import Paper  # Your Paper Pydantic model
from pages.shell import top_bar
from services.chat.chat_components import ChatBubble, CitationCard


# --- Pydantic Models ---
class SharedPageData(BaseModel):
    """Lightweight container of state used by the home-page template."""

    model_config = {"arbitrary_types_allowed": True}

    title: str = config.config.APP_TITLE
    current_query: Optional[str] = None
    search_results: Optional[list[Paper]] = None
    recent_topics: Optional[list] = None  # Topic rows for the home strip


# --- UI Components ---
def render_error_message(message: str, context: str = "general") -> Div:
    """Return a styled error banner."""
    return Div(
        P(message),
        cls=f"error-message error-context-{context}",
        style="color: #d9534f; background-color: #f9f2f2; border: 1px solid #d9534f; padding: 10px; border-radius: 4px; margin: 10px 0;",
    )


def render_report_config_form(
    original_query: str,
    selected_papers_json_list: list[str],
    num_selected: int,
    is_url_input: bool,
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
            BUTTON(
                "Generate Report",
                type="submit",
                id="generate-report-btn-top",
                style="margin-right:8px;",
            ),
            hx_post="/generate-report-incremental",
            hx_disabled_elt="#generate-report-btn-top",
            hx_target="#report-display-area",
            hx_swap="innerHTML",
            hx_include="[name='selected_papers_json'], #original_query_for_report_hidden, [name='is_url_input']",
        ),
    )


_AUTHOR_JUNK_RE = re.compile(
    r"(?i)\b(view profile|profile|university|institute|dept\.|department|"
    r"laborator(?:y|ies)|google|microsoft|meta ai|openai|deepmind)\b"
)


def _clean_author_list(authors: list[str]) -> list[str]:
    """Filter Exa-scraped author fields to plausible person names.

    Strips UI chrome ("View Profile"), institutions/labs, and junk tokens;
    splits concatenated names on "View Profile" boundaries; drops empties.
    """
    out: list[str] = []
    for raw in authors:
        for piece in re.split(r"(?i)view profile", str(raw)):
            piece = re.sub(r"\s+", " ", piece).strip(" ,;.|")
            # Truncate at institution/page-chrome boundary rather than
            # dropping the whole string — "Name University" -> "Name".
            piece = _AUTHOR_JUNK_RE.split(piece, maxsplit=1)[0].strip(" ,;.|")
            if not piece or len(piece) < 3 or len(piece) > 80:
                continue
            if not re.search(r"[A-Za-z]", piece):
                continue
            if piece not in out:
                out.append(piece)
    return out


def _render_paper_item(
    paper: Paper, is_main_paper: bool = False, wildcard: bool = False
) -> LI:
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
        except Exception as exc:
            logger.debug("date_format_failed", paper_id=paper.id, err=str(exc))
            formatted_date = str(paper.published_date)

    paper_json_str = paper.model_dump_json()

    # Exa's scraped author fields leak page chrome ("View Profile",
    # "SrinivasanPratul P." mashed names, institutions). Keep only plausible
    # name tokens for display.
    clean_authors = _clean_author_list(paper.authors or [])

    authors_display = "Author(s): N/A"
    if clean_authors:
        if (
            len(clean_authors) > config.config.MAX_AUTHORS_DISPLAY
        ):  # Using config.config
            authors_display = f"Author(s): {', '.join(clean_authors[: config.config.MAX_AUTHORS_DISPLAY])}, et al."
        else:
            authors_display = f"Author(s): {', '.join(clean_authors)}"

    # Basic styling for the paper item
    item_style = "margin-bottom: 1em; padding: 0.5em; border: 1px solid var(--container-border); position: relative;"
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

    # --------------------------------------------------
    # Relevance badge
    # --------------------------------------------------
    relevance_span = None
    if paper.relevance_score is not None:
        rs = paper.relevance_score
        if rs >= 0.75:
            badge_color = "var(--relevance-high, #22c55e)"
        elif rs >= 0.50:
            badge_color = "var(--relevance-mid, #eab308)"
        else:
            badge_color = "var(--relevance-low, #ef4444)"
        relevance_span = Span(
            f"{rs:.2f}",
            cls="relevance-badge",
            title=f"Relevance: {rs:.2f}",
            style=f"font-size:0.75em; font-weight:600; color:{badge_color}; border:1px solid {badge_color}; border-radius:4px; padding:1px 5px; margin-left:6px; vertical-align:middle;",
        )

    # ISO date for client-side sorting; lowercase authors for filtering
    date_sort_key = ""
    if paper.published_date:
        try:
            date_sort_key = (
                paper.published_date.isoformat()
                if isinstance(paper.published_date, datetime)
                else str(paper.published_date)
            )
        except Exception as exc:
            logger.debug("date_sort_format_failed", paper_id=paper.id, err=str(exc))

    authors_lower = ",".join(a.lower() for a in clean_authors)

    li_attrs: dict[str, Any] = {
        "style": item_style,
        "id": f"paper-{paper.id}",
        "data-paper-id": paper.id,
        "data-date": date_sort_key,
        "data-authors": authors_lower,
    }
    if wildcard:
        li_attrs["id"] = "wild-card-item"
        li_attrs["class"] = "wildcard-paper"

    # Extract a clean abstract from text_content (skip arXiv metadata boilerplate)
    abstract_text = ""
    if paper.text_content:
        raw_text = paper.text_content.strip()
        # Try to find an "Abstract" section
        m = re.search(
            r"(?i)(?:^|\n)\s*(?:abstract|summary)[:\s]*\n?\s*(.+?)(?:\n\s*\n|\n\s*(?:#{1,3}\s|introduction|1[\.\s]))",
            raw_text[:8000],
            re.DOTALL,
        )
        if m:
            abstract_text = m.group(1).strip()
        else:
            # Fallback: skip arXiv-style header lines (lines with "arXiv:", ">", "[Submitted")
            lines = raw_text[:3000].split("\n")
            content_lines = []
            for line in lines:
                stripped = line.strip()
                # Skip arXiv metadata header lines
                if re.match(
                    r"^(#|arXiv:|>\s|Computer Science|\[Submitted|\[v\d|Title:|Authors?:|Date:)",
                    stripped,
                ):
                    continue
                if stripped and len(stripped) > 30:
                    content_lines.append(stripped)
                if len(" ".join(content_lines)) > 2000:
                    break
            abstract_text = " ".join(content_lines)

        # No truncation — the modal dialog scrolls for long abstracts

    toggle_btn = (
        BUTTON(
            I(cls="fa-solid fa-chevron-down"),
            type="button",
            cls="abstract-toggle-btn",
            title="Show abstract",
            style="background:transparent;border:none;cursor:pointer;font-size:0.7rem;margin-left:6px;padding:2px 4px;color:var(--fg);opacity:0.6;vertical-align:middle;",
            **{"data-abstract": html.escape(abstract_text)} if abstract_text else {},
        )
        if abstract_text
        else Span()
    )

    # No longer inline — JS will create a popover from data-abstract

    # BibTeX citation data attributes
    bib_year = ""
    if paper.published_date:
        try:
            date_val = (
                paper.published_date.isoformat()
                if isinstance(paper.published_date, datetime)
                else str(paper.published_date)
            )
            bib_year = date_val.split("-")[0]
        except Exception as exc:
            logger.debug("bib_year_format_failed", paper_id=paper.id, err=str(exc))
    bibtex_btn = BUTTON(
        I(cls="fa-solid fa-quote-right"),
        type="button",
        cls="bibtex-copy-btn",
        title="Copy BibTeX citation",
        style="background:transparent;border:none;cursor:pointer;font-size:0.7rem;margin-left:4px;padding:2px 4px;color:var(--fg);opacity:0.6;vertical-align:middle;",
        **{
            "data-bib-title": paper.title or "",
            "data-bib-authors": "; ".join(clean_authors),
            "data-bib-year": bib_year,
            "data-bib-url": str(paper.url) if paper.url else "",
        },
    )

    # "Save to library" bookmark button
    save_btn = BUTTON(
        I(cls="fa-regular fa-bookmark"),
        type="button",
        cls="save-paper-btn",
        title="Save to library",
        style="background:transparent;border:none;cursor:pointer;font-size:0.7rem;margin-left:4px;padding:2px 4px;color:var(--fg);opacity:0.6;vertical-align:middle;",
        **{
            "data-paper-id": paper.id,
            "data-paper-title": paper.title or "",
            "data-paper-url": str(paper.url) if paper.url else "",
            "data-paper-authors": "; ".join(clean_authors),
        },
    )

    # "More like this" button — find similar papers
    similar_btn = BUTTON(
        I(cls="fa-solid fa-wand-magic-sparkles"),
        type="button",
        cls="similar-btn",
        title="Find similar papers",
        style="background:transparent;border:none;cursor:pointer;font-size:0.7rem;margin-left:4px;padding:2px 4px;color:var(--fg);opacity:0.6;vertical-align:middle;",
        **{
            "data-paper-url": str(paper.url) if paper.url else "",
            "data-paper-title": paper.title or "",
        },
    )

    # Citation count badge (from Semantic Scholar)
    citation_span = None
    if paper.citation_count is not None and paper.citation_count > 0:
        citation_span = Span(
            I(cls="fa-solid fa-quote-left", style="font-size:0.6em;margin-right:2px;"),
            f"{paper.citation_count:,}",
            cls="citation-badge",
            title=f"{paper.citation_count:,} citations (Semantic Scholar)",
            style="font-size:0.75em; color:var(--fg-muted, #888); margin-left:8px; vertical-align:middle;",
        )

    # Container for inline similar-paper recommendations (populated by JS)
    similar_container = Div(
        cls="similar-container",
        id=f"similar-{paper.id}",
        style="display:none;",
    )

    # Action buttons grouped into a hover row so the card stays readable
    actions_row = Span(
        toggle_btn,
        bibtex_btn,
        save_btn,
        similar_btn,
        cls="paper-actions",
    )

    return LI(
        Div(
            *selection_inputs,
            A(
                paper.title if paper.title else "No Title Provided",
                href=paper.url if paper.url else "#",
                target="_blank",
            ),
            relevance_span,
            enrich_span,
            actions_row,
            Div(
                EM(authors_display),
                cls="paper-authors",
            ),
            Div(
                Span(f"Published: {formatted_date}"),
                citation_span,
                selection_label,
                cls="paper-meta",
            ),
        ),
        similar_container,
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

        # Sort & filter controls (client-side)
        results_content.append(
            Div(
                Div(
                    BUTTON(
                        I(cls="fa-solid fa-arrow-down-short-wide"),
                        " Date ↓",
                        type="button",
                        id="sort-date-btn",
                        cls="btn-secondary",
                        title="Sort by date (newest first)",
                        style="font-size:0.8rem;padding:3px 8px;",
                    ),
                    BUTTON(
                        "Select All",
                        type="button",
                        id="select-all-btn",
                        cls="btn-secondary",
                        title="Select all papers",
                        style="font-size:0.8rem;padding:3px 8px;",
                    ),
                    BUTTON(
                        "Deselect All",
                        type="button",
                        id="deselect-all-btn",
                        cls="btn-secondary",
                        title="Deselect all papers",
                        style="font-size:0.8rem;padding:3px 8px;",
                    ),
                    style="display:inline-flex;gap:6px;align-items:center;",
                ),
                Div(
                    INPUT(
                        type="text",
                        id="author-filter-input",
                        placeholder="Filter by author…",
                        style="font-size:0.85rem;padding:3px 6px;width:14rem;",
                    ),
                    BUTTON(
                        I(cls="fa-solid fa-diagram-project"),
                        " Visualize",
                        type="button",
                        id="cluster-viz-btn",
                        cls="btn-secondary",
                        title="Cluster papers by topic similarity",
                        style="font-size:0.8rem;padding:3px 8px;margin-left:8px;",
                    ),
                    style="display:inline-flex;gap:6px;align-items:center;margin-left:12px;",
                ),
                # Cluster visualization container (populated by JS)
                Div(id="cluster-viz-container", style="display:none;margin:12px 0;"),
                style="display:flex;align-items:center;flex-wrap:wrap;margin-bottom:8px;gap:4px;",
                id="results-controls",
            )
        )

        # Main list that HTMX endpoints will target
        results_content.append(
            UL(
                *list_items,
                id="papers-list",
                style="list-style-type: none; padding-left: 0;",
            )
        )

        # Pager placeholder – client-side JS populates controls
        results_content.append(Div(id="pager", cls="pager", style="margin-top:10px;"))
        has_results = True
    else:
        pass

    if not has_results:
        results_content.append(
            Div(
                P(
                    "No papers found for this query. Try different keywords or broaden your search.",
                    style="opacity:0.7;",
                ),
                style="padding:24px;text-align:center;border:1px dashed var(--oh-border,#444);"
                "border-radius:8px;margin-top:12px;",
            )
        )

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
                            Option(
                                "Select Report Size",
                                value="",
                                disabled=True,
                                selected=True,
                            ),
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
                    hx_disabled_elt="#generate-report-btn",
                    hx_target="#report-display-area",
                    hx_swap="innerHTML",
                    hx_include="[name='selected_papers_json'], #original_query_for_report_hidden, [name='is_url_input']",
                ),
            ]
        )

    return Div(
        *clearing_divs,  # first ensure old report UI cleared
        *hidden_input_fields,
        *results_content,
    )


def loading_indicator_component() -> Div:
    """Spinner visible while search requests are in-flight — centered in card."""
    return Div(
        Div(cls="oh-spinner"),
        Span(
            "Searching the PaperVerse…",
            id="oh-loading-text",
            style="display:inline-block; margin-left:6px;",
        ),
        id="loading-indicator",
        cls="htmx-indicator",
        style="display:flex; align-items:center; justify-content:center; gap:8px; padding:1rem 0; width:100%;",
    )


def report_loading_indicator_component() -> Div:
    """Spinner shown while the server composes a report."""
    return Div(
        Div(cls="oh-spinner"),
        P("Writing report...", style="display:inline-block;"),
        id="report-loading-indicator",
        cls="htmx-indicator report-specific-indicator",
    )


def ask_loading_indicator_component() -> Div:
    """Spinner shown while waiting for direct LLM answer."""
    return Div(
        Div(cls="oh-spinner"),
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
                    hx-get="/viewer/{paper.id}?url={urllib.parse.quote_plus(str(paper.url))}"
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
    # "Your topics" strip — quick links back into existing workspaces.
    topics_strip = None
    if page_data.recent_topics:
        topics_strip = Div(
            Div(
                "Your topics",
                cls="oh-topics-strip-label",
            ),
            Div(
                *[
                    A(
                        t.title or t.seed_query or "Untitled",
                        href=f"/topics/{t.id}",
                        cls="oh-topic-pill",
                    )
                    for t in page_data.recent_topics
                ],
                cls="oh-topics-strip-pills",
            ),
            cls="oh-topics-strip",
        )
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
    else:  # No search performed yet — first-run onboarding panel
        example_queries = [
            "implicit neural representations",
            "papers by Yann LeCun",
            "https://arxiv.org/abs/1706.03762",
        ]
        results_display_content = Div(
            P(
                "A workbench for research questions.",
                style="font-size:1.15rem;font-weight:600;margin:0 0 0.4rem;",
            ),
            P(
                "Search a topic, a paper link, or an author — Office Hours builds "
                "a living topic page around it: the paper set, streamed reports, "
                "notes, a blog, and a chat that knows all of them.",
                style="font-size:0.9rem;color:var(--fg-muted,#888);max-width:36rem;margin:0 auto 0.9rem;",
            ),
            Div(
                Span("Try ", style="font-size:0.85rem;color:var(--fg-muted,#888);"),
                *[
                    BUTTON(
                        q,
                        type="button",
                        cls="oh-hero-chip",
                        data_q=q,
                        style="font-size:0.8rem;padding:3px 10px;margin:0 3px;border:1px solid #666;border-radius:999px;background:var(--container-bg);color:var(--fg);cursor:pointer;",
                    )
                    for q in example_queries
                ],
            ),
            Script(
                "document.addEventListener('click',function(ev){"
                "var b=ev.target.closest('.oh-hero-chip');if(!b)return;"
                "var i=document.getElementById('search_query');if(!i)return;"
                "i.value=b.getAttribute('data-q');"
                "var f=document.getElementById('paper-search-form');"
                "if(f){f.requestSubmit();}else{i.focus();}"
                "});"
            ),
            cls="empty-state",
            style="padding:1.5rem 1rem;",
        )

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
            Link(rel="stylesheet", href="/static/css/blog.css"),
            # Font Awesome (self-hosted) – required for icon glyphs
            Link(rel="stylesheet", href="/static/vendor/fontawesome/css/all.min.css"),
            Script(src="/static/js/settings.js"),
            Script(src="/static/js/htmx-url-reset.js", defer=True),
            # Modular JS entry-points
            Script(src="/static/js/main.js?v=2", type="module"),
            Script(src="/static/js/library.js?v=1", defer=True),
            Script(src="/static/js/cluster-viz.js", defer=True),
            # ohBlogRender lazy-loads KaTeX — the report stream's done event
            # calls renderKaTeX(#report-content) to typeset math.
            Script(src="/static/js/blog-view.js", defer=True),
            Script(src="/static/js/similar-papers.js", defer=True),
            # No inline init - handled in settings.js
        ),
        # Comma added above to separate Head component from next Body child
        top_bar(
            "",
            Select(
                Option("Audience", value="", disabled=True, selected=True),
                Option("Technical (PhD)", value="technical"),
                Option("Semi-Technical", value="semi_technical"),
                Option("Non-Technical", value="non_technical"),
                id="aud-select",
                name="audience",
            ),
            Select(
                Option("Model", value="", disabled=True, selected=True),
                *[
                    Option(cfg["display_name"], value=key)
                    for key, cfg in config.config.AVAILABLE_MODELS.items()
                ],
                id="model-select",
                name="model_key",
            ),
            Select(
                Option("Report length", value="", disabled=True),
                Option("S (Concise)", value="S"),
                Option("M (Standard)", value="M", selected=True),
                Option("L (Detailed)", value="L"),
                id="size-select",
                name="size",
                title="Report length",
            ),
            BUTTON(
                "💬 Assistant",
                id="open-global-chat",
                type="button",
                hx_get="/chat-modal?paper_id=global&title=Research%20Assistant",
                hx_target="#modal-container",
                hx_swap="innerHTML",
            ),
            BUTTON(
                I(cls="fa-solid fa-gear"),
                id="oh-settings-btn",
                type="button",
                title="Settings",
                style="border:none;background:transparent;cursor:pointer;font-size:1rem;padding:4px 8px;",
            ),
        ),
        Div(id="status-banner"),  # Placeholder for busy/alert banners
        Div(
            Div(
                P(
                    I("Enter a research topic, query, paper link, or author name:"),
                    style="margin-bottom:0.5rem;",
                ),
                FORM(  # Search Form
                    INPUT(
                        type="search",
                        id="search_query",
                        name="search_query",
                        required=True,
                        placeholder="e.g., 'transformer architecture overview' or paste an arXiv link",
                        value=page_data.current_query or "",
                        style="width:100%;max-width:44rem;padding:6px 10px;font-size:0.95rem;",
                    ),
                    # Filters row — date + sources dropdown (collapsible)
                    Details(
                        Summary("Search options", cls="oh-options-summary"),
                        Div(
                            Div(
                                Span("After ", cls="filter-label"),
                                INPUT(
                                    type="text",
                                    id="start_published_date",
                                    name="start_published_date",
                                    placeholder="YYYY-MM-DD",
                                    autocomplete="off",
                                    style="font-size:0.8rem;padding:2px 4px;width:9rem;",
                                ),
                            ),
                            # Custom dropdown-checkbox for sources
                            Div(
                                Span("Sources ", cls="filter-label"),
                                Div(
                                    BUTTON(
                                        "All sources ▾",
                                        type="button",
                                        id="sources-dropdown-btn",
                                        style="font-size:0.8rem;padding:2px 8px;cursor:pointer;background:var(--container-bg);border:1px solid #aaa;border-radius:3px;min-width:9rem;text-align:left;",
                                    ),
                                    Div(
                                        *[
                                            Label(
                                                INPUT(
                                                    type="checkbox",
                                                    name="include_domains",
                                                    value=dom,
                                                    id=f"src-{dom.split('.')[0]}",
                                                    cls="source-checkbox",
                                                ),
                                                Span(
                                                    f" {label}",
                                                    style="font-size:0.8rem;",
                                                ),
                                                style="display:flex;align-items:center;gap:4px;padding:3px 8px;cursor:pointer;",
                                            )
                                            for dom, label in [
                                                ("arxiv.org", "arXiv"),
                                                ("biorxiv.org", "bioRxiv"),
                                                ("medrxiv.org", "medRxiv"),
                                                ("ieeexplore.ieee.org", "IEEE"),
                                                ("dl.acm.org", "ACM"),
                                                ("openreview.net", "OpenReview"),
                                                ("aclanthology.org", "ACL"),
                                            ]
                                        ],
                                        id="sources-dropdown-menu",
                                        style="display:none;position:absolute;top:100%;left:0;z-index:50;background:var(--container-bg);border:1px solid #aaa;border-radius:4px;box-shadow:0 2px 8px rgba(0,0,0,0.15);padding:4px 0;min-width:10rem;",
                                    ),
                                    style="position:relative;display:inline-block;",
                                ),
                            ),
                            cls="search-form-filters",
                        ),
                        Label(
                            INPUT(
                                type="checkbox",
                                name="deep_search",
                                id="deep-search-toggle",
                                value="1",
                                style="margin:0;",
                            ),
                            Span(
                                " Deep Research",
                                style="font-size:0.78rem;color:var(--fg-muted,#aaa);",
                            ),
                            title="Slower but more comprehensive results (uses Exa deep search)",
                            style="display:inline-flex;align-items:center;gap:3px;margin-left:0.75rem;cursor:pointer;user-select:none;",
                        ),
                        cls="oh-search-options",
                    ),
                    # Action buttons
                    Div(
                        BUTTON(
                            "Search Papers",
                            type="submit",
                            name="mode",
                            value="auto",
                            id="search-papers-btn",
                            onclick="document.getElementById('oh-loading-text').textContent='Searching the PaperVerse...'",
                        ),
                        BUTTON(
                            I(cls="fa-solid fa-user-graduate"),
                            " Search by Author",
                            type="button",
                            id="search-author-btn",
                            cls="btn-secondary",
                            title="Search for a researcher by name (Semantic Scholar)",
                            style="font-size:0.85rem;padding:5px 10px;margin-left:6px;",
                            hx_post="/author/search",
                            hx_include="#paper-search-form",
                            hx_target="#search-results-container",
                            hx_swap="innerHTML",
                            **{"hx-indicator": "#loading-indicator"},
                            onclick=(
                                "var q=document.getElementById('search_query').value;"
                                "if(!q.trim()){"
                                "document.getElementById('search-results-container').innerHTML="
                                "\"<div class='error-banner'>Please enter an author name.</div>\";"
                                "event.stopImmediatePropagation();"
                                "}else{"
                                "document.getElementById('oh-loading-text').textContent='Searching authors...';"
                                "}"
                            ),
                        ),
                        style="margin-top:0.4rem;display:flex;align-items:center;",
                    ),
                    hx_post="/search",
                    hx_target="#search-results-container",
                    hx_swap="innerHTML",
                    hx_push_url="false",
                    **{"hx-trigger": "submit", "hx-indicator": "#loading-indicator"},
                    onsubmit="['report-display-area','report-config-area','ask-result-container'].forEach(function(id){var el=document.getElementById(id); if(el){el.innerHTML='';}});",
                    id="paper-search-form",
                    style="background:inherit;padding:0.5rem 0;",
                ),
                # "Your topics" quick-link strip (only when the owner has any)
                topics_strip,
                # Centered search spinner (outside search form so it spans the card)
                loading_indicator_component(),
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
                Div(id="modal-container"),
                # no extra class – outer search-card handles border
            ),
            cls="search-card",
        ),
        Footer(
            P(
                f"  {config.config.YEAR} {config.config.APP_TITLE}. v{config.config.VERSION}."
            ),
            style="padding:0.5rem 0; border-top:1px solid #ccc; text-align:center; font-size:0.75rem; color:#aaa;",
        ),
        **{"hx-push-url": "false"},
    )  # Close Body


def welcome_page(page_data: SharedPageData, next_url: str = "/") -> Body:
    """Dedicated sign-in landing page for unauthenticated visitors.

    Shown only when ``REQUIRE_LOGIN=1`` and the request has no auth cookie.
    Renders a single centered card with the app name, a one-line tagline,
    a "Continue with Google" button, and a handful of feature bullets.
    Uses the same Head/CSS as the home page so theme + font cascade match.
    """

    sign_in_href = "/auth/google/login?next=" + urllib.parse.quote(
        next_url, safe="/?&="
    )

    return Body(
        Head(
            Title(page_data.title),
            Link(rel="stylesheet", href="/static/css/spinners.css"),
            Link(rel="stylesheet", href="/static/css/base.css"),
            Link(rel="stylesheet", href="/static/css/top-bar.css"),
            Link(rel="stylesheet", href="/static/css/utilities.css"),
            Link(rel="stylesheet", href="/static/css/misc.css"),
            Link(rel="stylesheet", href="/static/css/welcome.css"),
            Link(rel="stylesheet", href="/static/vendor/fontawesome/css/all.min.css"),
            # settings.js handles the dark-mode toggle + persistence; we reuse
            # the same script so the welcome page respects the user's theme.
            Script(src="/static/js/settings.js"),
        ),
        top_bar(""),
        Div(
            Div(
                Div(
                    "Office Hours",
                    cls="welcome-brand",
                ),
                P(
                    "An AI research assistant for paper discovery, deep-dive "
                    "reports, and blog-style synthesis.",
                    cls="welcome-tagline",
                ),
                A(
                    Span("Continue with Google", cls="welcome-cta-label"),
                    href=sign_in_href,
                    cls="welcome-cta",
                    rel="nofollow",
                ),
                Div(
                    Div(
                        Span("🔎", cls="welcome-feature-icon"),
                        Span(
                            "Search the literature with semantic + keyword retrieval",
                            cls="welcome-feature-text",
                        ),
                        cls="welcome-feature",
                    ),
                    Div(
                        Span("📄", cls="welcome-feature-icon"),
                        Span(
                            "Generate audience-aware reports across multiple papers",
                            cls="welcome-feature-text",
                        ),
                        cls="welcome-feature",
                    ),
                    Div(
                        Span("✍️", cls="welcome-feature-icon"),
                        Span(
                            "Turn any report into a Distill-style blog with "
                            "diagrams and interactive charts",
                            cls="welcome-feature-text",
                        ),
                        cls="welcome-feature",
                    ),
                    cls="welcome-features",
                ),
                P(
                    "Private beta — sign in with the Google account you "
                    "shared with Nithu.",
                    cls="welcome-fineprint",
                ),
                cls="welcome-card",
            ),
            cls="welcome-shell",
        ),
        Footer(
            P(
                f"  {config.config.YEAR} {config.config.APP_TITLE}. v{config.config.VERSION}."
            ),
            style=(
                "padding:0.5rem 0; border-top:1px solid #ccc; text-align:center; "
                "font-size:0.75rem; color:#aaa;"
            ),
        ),
        **{"hx-push-url": "false"},
    )


# Close HTML
