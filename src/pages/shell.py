"""Shared page shell — one top-bar + head boilerplate for every app page.

Two consumers:
- f-string route pages (topics, library, history, report detail) use
  ``page_shell(title, body_html, active=...)`` for a full HTML document.
- FastHTML pages (home, settings, welcome) compose ``page_head(...)`` and
  ``top_bar(...)`` directly. String pages serialize these same components.

Every app page gets the same nav, dark-mode toggle, and auth-status slot —
previously the standalone HTML pages had no variables.css/settings.js so
dark mode and auth status silently didn't work there.
"""

from __future__ import annotations

import html
from typing import Any

from fasthtml.common import (
    A,
    Button,
    Div,
    Head,
    I,
    Link,
    Meta,
    NotStr,
    P,
    Script,
    Span,
    Title,
)

from utils.static_assets import static_url

# (label, href, key, fa icon, tooltip) — order is the nav order on every page.
NAV_LINKS: list[tuple[str, str, str, str, str]] = [
    (
        "Search",
        "/",
        "search",
        "fa-solid fa-magnifying-glass",
        "Start a research question",
    ),
    (
        "Library",
        "/topics",
        "library",
        "fa-solid fa-book",
        "Topics, saved papers & reports",
    ),
    (
        "Reports",
        "/reports",
        "reports",
        "fa-regular fa-file-lines",
        "Your research reports",
    ),
    (
        "Settings",
        "/settings",
        "settings",
        "fa-solid fa-gear",
        "Models, Notion & account",
    ),
]

# Sub-tabs inside the Library hub — Topics | Papers | Reports share one
# strip rendered at the top of each of the three pages. Topics leads:
# the topic is the product's unit, papers are its contents.
# (label, href, key, tooltip)
LIB_SUBTABS: list[tuple[str, str, str, str]] = [
    ("Topics", "/topics", "topics", "Research projects you're running"),
    ("Papers", "/library", "papers", "Your saved reading list"),
    ("Reports", "/reports", "reports", "Generated research write-ups"),
]


def lib_subtabs_html(active: str) -> str:
    """Sub-tab strip for the Library hub pages, with a one-line role note."""
    desc = {
        "papers": "Papers you bookmarked to read.",
        "topics": "Research projects that keep collecting papers over time.",
        "reports": "Write-ups snapshotted from a topic's paper set.",
    }
    parts = []
    for label, href, key, tip in LIB_SUBTABS:
        # oh-tip-below: the strip sits right under the sticky top bar, so an
        # above-tooltip renders behind it. Tip goes under the tab instead.
        cls = "lib-subtab oh-tip oh-tip-below" + (" active" if key == active else "")
        parts.append(
            f'<a href="{href}" class="{cls}" data-tip="{html.escape(tip, quote=True)}">'
            f"{html.escape(label)}</a>"
        )
    strip = '<div class="lib-subtabs">' + "".join(parts) + "</div>"
    note = (
        f'<div class="subtab-desc">{html.escape(desc.get(active, ""))}</div>'
        if desc.get(active)
        else ""
    )
    return strip + note


# Pinned client deps — every page loads the same versions.
_HTMX = "https://unpkg.com/htmx.org@1.9.12"
_HTMX_SSE = "https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js"
FONT_STYLESHEET = (
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;600;700&family=Instrument+Serif:ital@1&display=swap"
)

# Applies the saved theme before first paint so dark mode never flashes
# light on navigation — settings.js re-syncs the class after load.
THEME_INLINE_SCRIPT = (
    "(function(){try{if(localStorage.getItem('theme')==='dark')"
    "{document.documentElement.classList.add('oh-dark')}}catch(e){}})();"
)


def page_head(title: str, *extra: Any) -> Head:
    """One head for FastHTML and string pages; workspace rules load last.

    ``title`` is the full document title. Page-specific assets belong in
    ``extra`` rather than copies of the shared font/theme/client setup.
    """
    return Head(
        Meta(charset="utf-8"),
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Title(title),
        Script(THEME_INLINE_SCRIPT),
        Link(rel="preconnect", href="https://fonts.googleapis.com"),
        Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
        Link(rel="stylesheet", href=FONT_STYLESHEET),
        *[
            Link(rel="stylesheet", href=static_url(f"/static/css/{name}.css"))
            for name in (
                "variables",
                "base",
                "spinners",
                "top-bar",
                "utilities",
                "forms",
                "modal",
                "chat",
            )
        ],
        Link(
            rel="stylesheet",
            href=static_url("/static/vendor/fontawesome/css/all.min.css"),
        ),
        Script(src=_HTMX),
        Script(src=_HTMX_SSE),
        Script(src=static_url("/static/js/settings.js"), defer=True),
        *extra,
        Link(rel="stylesheet", href=static_url("/static/css/workspace.css")),
        Script(src=static_url("/static/js/workspace.js"), defer=True),
    )


def nav_links_html(active: str = "") -> str:
    """Nav anchor markup — used by both the f-string shell and the
    FastHTML top_bar (same output, so every page's nav is identical)."""
    return "\n".join(str(link) for link in nav_links(active))


def top_bar_html(active: str = "", extra_html: str = "") -> str:
    """Full top-bar markup (brand + nav + auth status + dark toggle)."""
    return str(top_bar(active, NotStr(extra_html)))


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
{page_head(f"{title} · Office Hours", NotStr(head_extra))}
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
            Span(label),
            href=href,
            cls="top-bar-link oh-tip oh-tip-below"
            + (" active" if key == active else ""),
            aria_label=label,
            data_tip=tip,
        )
        for label, href, key, icon, tip in NAV_LINKS
    ]


def workspace_rail(topics: list[Any], active_id: str = "") -> Div:
    """A quiet, real-data topic index shared by home and topic workspaces."""
    return Div(
        Span("TOPICS", cls="workspace-rail-heading"),
        *[
            A(
                Span(f"{i + 1:02}", cls="workspace-topic-number"),
                Span(topic.title or topic.seed_query or "Untitled topic"),
                href=f"/topics/{topic.id}",
                cls="workspace-topic" + (" selected" if topic.id == active_id else ""),
            )
            for i, topic in enumerate(topics[:8])
        ],
        A("All topics →", href="/topics", cls="workspace-all-topics"),
        A("Start a new question", href="/", cls="workspace-new-question"),
        P("Your papers, reports and notes stay together.", cls="workspace-rail-note"),
        cls="workspace-rail",
        role="navigation",
        aria_label="Research topics",
    )


def library_hub(inner_html: str, topics: list[Any], active_id: str = "") -> str:
    """Library hub pages (topics, papers, reports) in the workspace layout:
    the shared topic rail beside one sheet, matching the topic workspace."""
    return (
        '<div class="marble-workspace topic-workspace library-hub">'
        f"{workspace_rail(topics, active_id)}"
        f'<section class="workspace-topic-sheet">{inner_html}</section></div>'
    )


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
        Div("", cls="top-bar-spacer", style="flex:1"),
        *extra_controls,
        Button(
            I(cls="fa-solid fa-comment-dots"),
            cls="oh-assistant-btn oh-tip oh-tip-below",
            type="button",
            aria_label="Research Assistant",
            data_tip="Ask questions across your papers & reports",
            hx_get="/chat-modal?paper_id=global&title=Research%20Assistant",
            hx_target="#modal-container",
            hx_swap="innerHTML",
        ),
        Span(id="oh-auth-status", cls="top-auth-status"),
        Button(
            I(cls="fa-solid fa-moon"),
            id="oh-dark-toggle",
            cls="oh-theme-toggle",
            aria_label="Toggle dark mode",
            type="button",
        ),
        cls="top-bar",
    )
