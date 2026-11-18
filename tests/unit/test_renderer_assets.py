from pathlib import Path


def test_renderers_use_pinned_browser_highlighter_build():
    root = Path(__file__).resolve().parents[2]
    browser_build = "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"
    for path in ["static/js/blog-view.js", "static/js/chat/markdown.js"]:
        source = (root / path).read_text()
        assert browser_build in source
        assert "/npm/highlight.js@11/highlight.min.js" not in source


def test_blog_sections_share_dependency_loading_promise():
    root = Path(__file__).resolve().parents[2]
    source = (root / "static/js/blog-view.js").read_text()
    assert "if (_depsPromise) return _depsPromise;" in source
    assert "_depsPromise = (async () =>" in source
    assert "return _depsPromise;" in source


def test_renderers_highlight_dom_after_dependencies_load():
    root = Path(__file__).resolve().parents[2]
    source = (root / "static/js/blog-view.js").read_text()
    assert "highlight: function" not in source
    assert "pre code:not([data-highlighted])" in source
    assert "hljs.highlightElement(block)" in source
    assert "await loadAllDeps();\n  highlightCode(root);" in source
    assert source.count("highlightCode(articleEl);") == 2
    assert '["htmx:afterSwap", "htmx:sseMessage"]' in source
    for path in [
        "src/api/routes/history.py",
        "src/api/routes/topics.py",
        "src/api/routers/report_stream.py",
    ]:
        assert "r.renderContent(document.getElementById(" in (root / path).read_text()


def test_report_copy_protects_code_from_prose_whitespace_cleanup():
    root = Path(__file__).resolve().parents[2]
    source = (root / "static/js/report.js").read_text()
    assert 'el.tagName === "PRE" || el.tagName === "CODE"' in source
    assert "codeBlocks.push(el.textContent);" in source
    assert source.index(".trim()\n    .split(codeMarker)") < source.index(
        '.map((text, index) => text + (codeBlocks[index] ?? ""))'
    )
