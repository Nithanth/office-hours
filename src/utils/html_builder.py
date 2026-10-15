"""Common HTML-building helpers used across services."""

from __future__ import annotations

__all__: list[str] = ["text_to_html_paragraphs"]


def text_to_html_paragraphs(text: str | None) -> str:
    """Convert *text* into sanitized markdown-rendered HTML.

    LLM output is markdown (**bold**, lists, links, $math$); rendering it here
    upgrades every caller (report blocks, topic overview, direct answers).

    Blank or *None* input returns an explanatory placeholder paragraph so the UI
    never renders an empty div.
    """

    if not text or not text.strip():
        return "<p><em>Summary could not be generated or is empty.</em></p>"

    from utils.md_render import render_markdown_safe

    return str(render_markdown_safe(text))
