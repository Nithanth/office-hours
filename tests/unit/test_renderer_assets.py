from pathlib import Path


def test_renderers_use_pinned_browser_highlighter_build():
    root = Path(__file__).resolve().parents[2]
    browser_build = "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"
    for path in ["static/js/blog-view.js", "static/js/chat/markdown.js"]:
        source = (root / path).read_text()
        assert browser_build in source
        assert "/npm/highlight.js@11/highlight.min.js" not in source
