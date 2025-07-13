"""Reusable HTML base layout used across Lux Scholar pages."""

from typing import Any

from fasthtml.common import (
    Body,
    Div,
    Footer,
    Head,
    Header,
    Html,
    Link,
    Main,
    Meta,
    Option,
    P,
    Script,
    Select,
    Title,
)

from loguru import logger

import config  # Provides AVAILABLE_MODELS mapping

# Structured logger bound to template component
logger = logger.bind(component="TemplateBase")


def base_layout(title_text: str, *content: Any) -> str:
    """A basic HTML layout component (prepends <!DOCTYPE html>)."""
    logger.debug("rendering base layout", title=title_text)

    html_doc = Html(
        Head(
            Meta(charset="UTF-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
            Title(title_text),
            Link(rel="stylesheet", href="/static/css/base.css"),
            Link(rel="stylesheet", href="/static/css/top-bar.css"),
            Link(rel="stylesheet", href="/static/css/utilities.css"),
            Link(rel="stylesheet", href="/static/css/modal.css"),
            Link(rel="stylesheet", href="/static/css/chat.css"),
            Link(rel="stylesheet", href="/static/css/forms.css"),
            Link(rel="stylesheet", href="/static/css/flatpickr-dark.css"),
            Link(rel="stylesheet", href="/static/css/misc.css"),
            # HTMX core + SSE extension and JS pool helper
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Script(src="https://unpkg.com/htmx.org@1.9.10/dist/ext/sse.js"),
            Script(src="/static/js/htmx-sse-pool.js"),
            # Optional utilities
            Script(src="https://cdn.jsdelivr.net/npm/lz-string@1.5.0/libs/lz-string.min.js"),
            # Icon set
            Link(
                rel="stylesheet",
                href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css",
            ),
        ),
        Body(
            Header(
                Div(
                    Link(title_text, href="/", cl="text-lg font-bold mr-6"),
                    Div("", cl="flex-1"),
                    Select(
                        Option("Select Audience", value="", disabled=True, selected=True),
                        Option("Highly Technical (PhD/Researcher)", value="technical"),
                        Option("Semi-Technical (Lux)", value="semi_technical"),
                        Option("Non-technical (Other VCs)", value="non_technical"),
                        id="aud-select",
                        cl="mr-3",
                    ),
                    Select(
                        Option("Select Model", value="", disabled=True, selected=True),
                        *[
                            Option(
                                cfg["display_name"],
                                value=key,
                            )
                            for key, cfg in config.config.AVAILABLE_MODELS.items()
                        ],
                        id="model-select",
                        cl="mr-3",
                    ),
                    Link(
                        "Clear",
                        href="#",
                        id="clear-prefs-btn",
                        cl="text-sm text-blue-500 mr-3 underline",
                    ),
                    Script(""),  # Placeholder for dynamic JS
                    cl="flex items-center gap-2",
                ),
                cl="top-bar flex items-center px-4",
            ),
            Main(
                Div(*content, cl="container mx-auto p-4"),
            ),
            Footer(
                P(" 2024 Research Paper Analysis Tool. All rights reserved."),
                cl="text-center text-sm text-gray-500 py-4",
            ),
            Script(src="/static/js/main.js", type="module"),
            Script(src="/static/js/settings.js"),
            cl="bg-gray-100",
        ),
    )
    return "<!DOCTYPE html>" + str(html_doc)
