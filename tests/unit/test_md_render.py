import re

import pytest

from utils.md_render import render_markdown_safe

_HANDLER = re.compile(r"(?i)<[^>]*\son\w+\s*=")
_SCRIPT_URL = re.compile(
    r"(?i)<[^>]*(href|src)\s*=\s*\"?\s*(javascript|vbscript|data:text)"
)


@pytest.mark.parametrize(
    "payload",
    [
        "<img src=x/onerror=alert(1)>",
        '<a href="jav&#x61;script:alert(1)">x</a>',
        "<svg><animate onbegin=alert(1)>",
        "<math><mtext><img src=x onerror=alert(1)></mtext></math>",
        "<details open ontoggle=alert(1)>x</details>",
        "<script>alert(1)</script>",
        "<iframe src=//evil></iframe>",
        "[x](javascript:alert(1))",
        "[x](JaVaScRiPt&colon;alert(1))",
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        "![x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
        "para {: onclick=alert(1) }",
        '<p style="background:url(javascript:alert(1))">x</p>',
        '<div id="blog-pane-article" class="hidden">x</div>',
    ],
)
def test_xss_payloads_are_neutralised(payload: str) -> None:
    out = render_markdown_safe(payload)
    assert not _HANDLER.search(out), out
    assert not _SCRIPT_URL.search(out), out
    for tag in ("<script", "<svg", "<math", "<iframe", "<details", "<animate"):
        assert tag not in out.lower(), out
    assert "style=" not in out
    assert "blog-pane-article" not in out and 'class="hidden"' not in out


def test_markdown_features_survive() -> None:
    md = (
        "# Title\n\n**bold** and *em* with [link](https://example.com) and $x^2$.\n\n"
        "| a | b |\n|:--|--:|\n| 1 | 2 |\n\n"
        "```python\nprint(1)\n```\n\n"
        "Note[^1]\n\n[^1]: footnote text\n\n"
        "![fig](data:image/png;base64,iVBORw0KGgo=)\n"
    )
    out = render_markdown_safe(md)
    assert "<h3>Title</h3>" in out
    assert "<strong>bold</strong>" in out and "<em>em</em>" in out
    assert 'href="https://example.com"' in out
    assert "$x^2$" in out
    assert "<table>" in out and 'style="text-align:left"' in out
    assert 'class="language-python"' in out
    assert 'id="fn:1"' in out and 'href="#fn:1"' in out
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in out
