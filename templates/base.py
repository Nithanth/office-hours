from typing import Any

from fasthtml.common import (
    H1,
    Body,
    Div,
    Footer,
    Head,
    Header,
    Html,
    Link,
    Main,
    Meta,
    P,
    Script,
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
#                     'sha512-nGwAlo1EX7G34KMW1zzNfX6NlC4D2jVqjS2Q3n_C8shChPZ12o9zUmlXn5GfQJ5k1R29E2TYz79E2YGFWJcvvw==')

# Example: Tailwind via CDN (can be useful for rapid prototyping)
# tailwind_script = Script(src="https://cdn.tailwindcss.com")


def base_layout(title_text: str, *content: Any) -> Html:
    """A basic HTML layout component."""
    return Html(
        Head(
            Meta(charset="UTF-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
            Title(title_text),
            Link(rel="stylesheet", href="/static/css/styles.css"),
            Script(src="https://unpkg.com/htmx.org@1.9.10"),
            Script(src="https://unpkg.com/htmx.org/dist/ext/sse.js"),
            Script(src="/static/js/htmx-sse-pool.js"),
            # For localStorage compression of chat transcripts
            Script(src="https://cdn.jsdelivr.net/npm/lz-string@1.5.0/libs/lz-string.min.js"),
            # tailwind_script, # Uncomment if you want to use Tailwind CSS via CDN
            # htmx_script, # FastHTML usually includes HTMX, uncomment if needed explicitly
        ),
        Body(cl="bg-gray-100"),  # Example Tailwind class, won't apply without Tailwind
        Body(
            Header(H1(title_text), cl="bg-slate-700 p-4 text-center"),
            Main(
                Div(*content, cl="container mx-auto p-4"),  # Basic container class from styles.css
            ),
            Footer(
                P(" 2024 Research Paper Analysis Tool. All rights reserved."),
                cl="text-center text-sm text-gray-500 py-4",
            ),
            Script(src="/static/js/app.js"),  # Load custom JS
        ),
    )
