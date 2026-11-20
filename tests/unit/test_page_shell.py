"""App documents must not drift between FastHTML and string rendering."""

from html.parser import HTMLParser

import pytest

from pages.main_page import SharedPageData, home_page, welcome_page
from pages.settings_page import settings_page
from pages.shell import FONT_STYLESHEET, page_shell, top_bar, top_bar_html


class Document(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = []
        self.feed(str(markup))

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    ("render", "active"),
    [
        (lambda: home_page(SharedPageData()), "search"),
        (lambda: welcome_page(SharedPageData()), None),
        (lambda: settings_page(pins={}, openrouter_key=None), "settings"),
        (lambda: page_shell("Reports", "<p>Report</p>", active="reports"), "reports"),
    ],
)
def test_every_app_document_has_one_shared_head_and_chrome(render, active):
    elements = Document(render()).elements
    viewports = [
        a for tag, a in elements if tag == "meta" and a.get("name") == "viewport"
    ]
    assert viewports == [
        {"name": "viewport", "content": "width=device-width, initial-scale=1"}
    ]
    links = [a.get("href", "") for tag, a in elements if tag == "link"]
    assert links.count(FONT_STYLESHEET) == 1
    for asset in ("variables", "base", "top-bar", "workspace", "controls"):
        assert sum(f"/static/css/{asset}.css?v=" in href for href in links) == 1
    scripts = [a["src"] for tag, a in elements if tag == "script" and "src" in a]
    assert scripts.count("https://unpkg.com/htmx.org@1.9.12") == 1
    assert scripts.count("https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js") == 1
    for asset in ("settings", "workspace", "controls"):
        assert sum(f"/static/js/{asset}.js?v=" in src for src in scripts) == 1
    assert sum(a.get("id") == "modal-container" for _, a in elements) == 1
    selected = [
        a["href"]
        for tag, a in elements
        if tag == "a"
        and "top-bar-link" in a.get("class", "").split()
        and "active" in a["class"].split()
    ]
    assert selected == (
        []
        if active is None
        else [{"search": "/", "settings": "/settings", "reports": "/reports"}[active]]
    )


def test_string_shell_preserves_extensions_and_escapes_titles():
    markup = page_shell(
        "A <topic>",
        "<article>Report</article>",
        head_extra='<meta name="page-specific" content="yes">',
        scripts='<script src="/page-specific.js"></script>',
    )
    assert "<title>A &lt;topic&gt; · Office Hours</title>" in markup
    assert '<meta name="page-specific" content="yes">' in markup
    assert '<script src="/page-specific.js"></script>' in markup
    assert "<article>Report</article>" in markup
    assert top_bar_html("settings") == str(top_bar("settings"))
