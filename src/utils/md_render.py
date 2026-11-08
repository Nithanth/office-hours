"""Sanitized server-side markdown → HTML.

Shared by the topic workspace sections, the public share page, and the
report/text renderer. Markdown input is LLM output that a user can steer, and
Python-Markdown passes raw HTML through untouched, so the *rendered* HTML is
run through an allowlist sanitizer (nh3/ammonia): only the tags, attributes
and URL schemes listed below survive; everything else is dropped.
"""

from __future__ import annotations

import re

import markdown as _markdown
import nh3

_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "dd", "del", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol",
    "p", "pre", "s", "samp", "small", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}  # fmt: skip

_ALLOWED_ATTRIBUTES = {
    "*": {"class", "id", "title"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "ol": {"start"},
    "td": {"align", "colspan", "rowspan", "style"},
    "th": {"align", "colspan", "rowspan", "style"},
}

# "data" is admitted only so blog figures (generated PNG data URLs) render;
# _filter_attribute restricts it to raster images in <img src>.
_URL_SCHEMES = {"http", "https", "mailto", "data"}
_URL_ATTRS = {"href", "src"}
_DATA_IMAGE = re.compile(r"(?i)^\s*data:image/(png|jpeg|jpg|gif|webp);base64,")
# Only ids/classes Python-Markdown emits itself (footnotes, fenced code) — an
# arbitrary id or class could clobber or restyle the host page's own elements.
_SAFE_ID = re.compile(r"^fn(ref)?:[\w.-]+$")
_SAFE_CLASS = re.compile(r"^(language-[\w+-]+|footnote(-ref|-backref)?)$")


def _filter_attribute(tag: str, attr: str, value: str) -> str | None:
    if attr in _URL_ATTRS and value.strip().lower().startswith("data:"):
        return value if tag == "img" and _DATA_IMAGE.match(value) else None
    if attr == "id":
        return value if _SAFE_ID.match(value) else None
    if attr == "class":
        kept = [c for c in value.split() if _SAFE_CLASS.match(c)]
        return " ".join(kept) or None
    return value


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        attribute_filter=_filter_attribute,
        filter_style_properties={"text-align"},
        link_rel="noopener noreferrer nofollow",
    )


def render_markdown_safe(md: str) -> str:
    rendered = str(
        _markdown.markdown(md, extensions=["extra", "sane_lists"], output_format="html")
    )
    safe = sanitize_html(rendered)
    # LLM headings must not outrank the page's own structure — clamp h1/h2 to h3.
    return re.sub(r"<(/?)h[12]\b", r"<\1h3", safe)
