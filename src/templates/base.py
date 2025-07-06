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

# Define Cdn if not already part of your FastHTML/PyHTML common imports
# Some versions of FastHTML might have this or similar utilities built-in.
# If Cdn is not recognized, you might need to ensure it's correctly imported or defined.
# def Cdn(name, version, file, integrity=None, cross_origin='anonymous', referrer_policy='no-referrer'):
#     """Returns a script tag for a CDN."""
#     prefix = f'https://cdnjs.cloudflare.com/ajax/libs/{name}/{version}/{file}'
#     return Script(src=prefix, integrity=integrity, cross_origin=cross_origin, referrer_policy=referrer_policy)
# htmx_script = Cdn('htmx.org', '1.9.10', 'htmx.min.js',
#                     'sha512-nGwAlo1EX7GfQJ5k1R29E2TYz79E2YGFWJcvvw==')
# Example: Tailwind via CDN (can be useful for rapid prototyping)
# tailwind_script = Script(src="https://cdn.tailwindcss.com")
import config  # Needed for model dropdown values


def base_layout(title_text: str, *content: Any) -> Html:
    """A basic HTML layout component."""
    return Html(
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
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Script(src="https://unpkg.com/htmx.org/dist/ext/sse.js"),
            Script(src="/static/js/htmx-sse-pool.js"),
            # For localStorage compression of chat transcripts
            Script(src="https://cdn.jsdelivr.net/npm/lz-string@1.5.0/libs/lz-string.min.js"),
            # tailwind_script, # Uncomment if you want to use Tailwind CSS via CDN
            # htmx_script, # FastHTML usually includes HTMX, uncomment if needed explicitly
        ),
        Body(
            # Global toolbar – logo left, controls right
            Header(
                Div(
                    # Left: logo / home link
                    Link(title_text, href="/", cl="text-lg font-bold mr-6"),
                    # Spacer pushes controls to right
                    Div("", cl="flex-1"),
                    # Audience select
                    Select(
                        Option("Select Audience", value="", disabled=True, selected=True),
                        Option("Highly Technical (PhD/Researcher)", value="technical"),
                        Option("Semi-Technical (Lux)", value="semi_technical"),
                        Option("Non-technical (Other VCs)", value="non_technical"),
                        id="aud-select",
                        cl="mr-3",
                    ),
                    # Model select – built from config at runtime for flexibility
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
                    # Theme toggle button (inserted by JS later)
                    Script(""),
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
            # Modular frontend scripts (Phase 3 refactor)
            Script(src="/static/js/shuffle_controls.js"),
            Script(src="/static/js/main.js", type="module"),
            Script(src="/static/js/app.js"),
            Script(src="/static/js/settings.js"),
            cl="bg-gray-100",
        ),
    )
