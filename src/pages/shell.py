"""Shared page shell — one top-bar + head boilerplate for every app page.

Two consumers:
- f-string route pages (topics, library, history, report detail) use
  ``page_shell(title, body_html, active=...)`` for a full HTML document.
- FastHTML pages (home, settings, welcome) compose ``top_bar(...)`` from
  fasthtml components via ``nav_links(active)``.

Every app page gets the same nav, dark-mode toggle, and auth-status slot —
previously the standalone HTML pages had no variables.css/settings.js so
dark mode and auth status silently didn't work there.
"""

from __future__ import annotations

import html
from typing import Any

from fasthtml.common import A, Button, Div, I, Span

from utils.static_assets import static_url

# (label, href, key, fa icon) — order is the nav order on every page.
NAV_LINKS: list[tuple[str, str, str, str]] = [
    ("Library", "/library", "library", "fa-solid fa-book"),
    ("Settings", "/settings", "settings", "fa-solid fa-gear"),
]

# Sub-tabs inside the Library hub — Papers | Topics | Reports share one
# strip rendered at the top of each of the three pages.
LIB_SUBTABS: list[tuple[str, str, str]] = [
    ("Papers", "/library", "papers"),
    ("Topics", "/topics", "topics"),
    ("Reports", "/reports", "reports"),
]


def lib_subtabs_html(active: str) -> str:
    """Sub-tab strip for the Library hub pages."""
    parts = []
    for label, href, key in LIB_SUBTABS:
        cls = "lib-subtab" + (" active" if key == active else "")
        parts.append(f'<a href="{href}" class="{cls}">{html.escape(label)}</a>')
    return '<div class="lib-subtabs">' + "".join(parts) + "</div>"


# Pinned client deps — every page loads the same versions.
_HTMX = "https://unpkg.com/htmx.org@1.9.12"
_HTMX_SSE = "https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js"

_HEAD_LINKS = (
    f'<link rel="stylesheet" href="{static_url("/static/css/base.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/variables.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/top-bar.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/utilities.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/forms.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/modal.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/css/chat.css")}">\n'
    f'  <link rel="stylesheet" href="{static_url("/static/vendor/fontawesome/css/all.min.css")}">\n'
    f'  <script src="{_HTMX}"></script>\n'
    f'  <script src="{_HTMX_SSE}"></script>\n'
    f'  <script src="{static_url("/static/js/settings.js")}" defer></script>'
)


def nav_links_html(active: str = "") -> str:
    """Nav anchor markup — used by both the f-string shell and the
    FastHTML top_bar (same output, so every page's nav is identical)."""
    parts = []
    for label, href, key, icon in NAV_LINKS:
        cls = "top-bar-link" + (" active" if key == active else "")
        parts.append(
            f'<a href="{href}" class="{cls}" title="{html.escape(label, quote=True)}" '
            f'aria-label="{html.escape(label, quote=True)}"><i class="{icon}"></i></a>'
        )
    return "\n    ".join(parts)


def top_bar_html(active: str = "", extra_html: str = "") -> str:
    """Full top-bar markup (brand + nav + auth status + dark toggle)."""
    return f"""<div class="top-bar">
    <a href="/" class="top-bar-brand">█▓▒░ Office Hours ░▒▓█</a>
    {nav_links_html(active)}
    <div style="flex:1"></div>
    {extra_html}
    <span id="oh-auth-status" class="top-auth-status"></span>
    <button id="oh-dark-toggle" type="button" aria-label="Toggle dark mode">🌙 Dark</button>
  </div>"""


def page_shell(
    title: str,
    body_html: str,
    *,
    active: str = "",
    head_extra: str = "",
    scripts: str = "",
    wrap_class: str = "page-wrap",
) -> str:
    """Full HTML document for f-string route pages. ``head_extra`` carries
    page-specific <link>/<style>; ``scripts`` carries page JS."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>{html.escape(title)} · Office Hours</title>
  {_HEAD_LINKS}
  {head_extra}
</head>
<body>
  {top_bar_html(active)}
  <main class="{wrap_class}">
    {body_html}
  </main>
  <div id="modal-container"></div>
  {scripts}
</body>
</html>"""


# ---------------------------------------------------------------------------
# FastHTML components — same markup for the FT pages.
# ---------------------------------------------------------------------------


def nav_links(active: str = "") -> list:
    # `A`, not `Link`: fasthtml's Link emits a void <link> element, so the
    # anchors silently dropped href/class and only their text rendered.
    return [
        A(
            I(cls=icon),
            href=href,
            cls="top-bar-link" + (" active" if key == active else ""),
            title=label,
            aria_label=label,
        )
        for label, href, key, icon in NAV_LINKS
    ]


def top_bar(active: str = "", *extra_controls: Any) -> Div:
    """Top-bar component for FastHTML pages. ``extra_controls`` are the
    page-specific widgets rendered between the nav and the auth/dark
    cluster (e.g. the home page's audience/model selects)."""
    return Div(
        A(
            "█▓▒░ Office Hours ░▒▓█",
            href="/",
            cls="top-bar-brand",
        ),
        *nav_links(active),
        Div("", style="flex:1"),
        *extra_controls,
        Span(id="oh-auth-status", cls="top-auth-status"),
        Button(
            "🌙 Dark",
            id="oh-dark-toggle",
            aria_label="Toggle dark mode",
            type="button",
        ),
        cls="top-bar",
    )
