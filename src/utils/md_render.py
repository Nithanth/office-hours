"""Sanitized server-side markdown → HTML.

Shared by the topic workspace sections and the report/text renderer. The
sanitizer strips active-content tags, on* handlers, and javascript: URLs from
LLM output before parsing — markdown.markdown() does not escape raw HTML.
"""

from __future__ import annotations

import re

import markdown as _markdown

_DANGEROUS = re.compile(
    r"(?is)</?\s*(script|iframe|object|embed|form|style|link|meta|base)[^>]*>"
)
_HANDLERS = re.compile(r"(?is)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_JS_SCHEME = re.compile(r"(?i)javascript\s*:")

# LLM headings must not outrank the page's own structure — clamp h1/h2 to h3.
_CLAMP_H = re.compile(r"</?h[12](?=[\s>])")


def render_markdown_safe(md: str) -> str:
    safe = _JS_SCHEME.sub("", _HANDLERS.sub("", _DANGEROUS.sub("", md)))
    rendered: str = str(
        _markdown.markdown(
            safe, extensions=["extra", "sane_lists"], output_format="html"
        )
    )
    rendered = re.sub(r"<(/?)h[12]", r"<\1h3", rendered)
    return rendered
