"""Common HTML-building helpers used across services."""

from __future__ import annotations

import html

__all__: list[str] = ["text_to_html_paragraphs"]


def text_to_html_paragraphs(text: str | None) -> str:
    """Convert *text* into escaped <p>paragraphs</p>.

    Blank or *None* input returns an explanatory placeholder paragraph so the UI
    never renders an empty div.
    """

    if not text or not text.strip():
        return "<p><em>Summary could not be generated or is empty.</em></p>"

    # Basic markdown-ish parsing ---------------------------------------------------
    lines: list[str] = [ln.rstrip() for ln in text.split("\n")]
    html_parts: list[str] = []
    list_open: str | None = None  # "ul" or "ol"

    def _close_list() -> None:  # local helper
        nonlocal list_open
        if list_open:
            html_parts.append(f"</{list_open}>")
            list_open = None

    for raw in lines:
        ln = raw.strip()

        # Empty line -> close any open list + skip
        if not ln:
            _close_list()
            continue

        # Headings (#, ##, ###) ---------------------------------------------------
        if ln.startswith("#"):
            _close_list()
            level = min(len(ln) - len(ln.lstrip("#")), 6)
            content = ln[level:].strip()
            if not content:
                continue  # skip empty headings
            # Clamp headings to h3-h6 so LLM output never creates h1/h2
            # (those are reserved for the report's own structure)
            clamped = max(level, 3)
            html_parts.append(f"<h{clamped}>{html.escape(content)}</h{clamped}>")
            continue

        # Ordered list item ("1. foo", "10. bar", etc.) -------------------------
        if ln[0].isdigit():
            # Find where digits end, then check for "." immediately after
            i = 0
            while i < len(ln) and ln[i].isdigit():
                i += 1
            if i < len(ln) and ln[i] == ".":
                if list_open != "ol":
                    _close_list()
                    list_open = "ol"
                    html_parts.append("<ol>")
                content = ln.split(".", 1)[1].lstrip()
                html_parts.append(f"<li>{html.escape(content)}</li>")
                continue

        # Unordered list item ("- " or "* ") ------------------------------------
        if ln.startswith(("- ", "* ")):
            if list_open != "ul":
                _close_list()
                list_open = "ul"
                html_parts.append("<ul>")
            content = ln[2:].strip()
            html_parts.append(f"<li>{html.escape(content)}</li>")
            continue

        # Default: plain paragraph -------------------------------------------------
        _close_list()
        html_parts.append(f"<p>{html.escape(ln)}</p>")

    _close_list()

    return "\n".join(html_parts)
