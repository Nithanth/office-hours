"""Unit tests for `_clean_blog_markdown` post-processing.

Locks in the behaviour that strips chat-style preamble/postamble that LLMs
sometimes emit despite the prompt forbidding it (e.g. "Here is a compelling
blog post:" leaking into the H1).
"""

from __future__ import annotations

from api.routers.report_notes import _clean_blog_markdown, _normalise_blog_title


def test_clean_keeps_body_when_no_preamble() -> None:
    md = "# Real Title\n\nFirst paragraph.\n\n## Section\n\nMore."
    assert _clean_blog_markdown(md) == md


def test_clean_strips_here_is_preamble_before_h1() -> None:
    md = (
        "Here is a compelling blog post based on the research report:\n\n"
        "# Real Title\n\nFirst paragraph."
    )
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Real Title"), cleaned
    assert "Here is a compelling" not in cleaned


def test_clean_strips_below_is_preamble() -> None:
    md = "Below is the polished blog post you requested:\n\n" "# Title\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Title")


def test_clean_strips_sure_preamble() -> None:
    md = "Sure, here's a blog post:\n\n" "# Title\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Title")


def test_clean_unwraps_full_response_code_fence() -> None:
    md = "```markdown\n# Title\n\nBody.\n```"
    cleaned = _clean_blog_markdown(md)
    assert cleaned == "# Title\n\nBody."


def test_clean_unwraps_bare_triple_fence_response() -> None:
    md = "```\n# Title\n\nBody.\n```"
    cleaned = _clean_blog_markdown(md)
    assert cleaned == "# Title\n\nBody."


def test_clean_strips_trailing_meta_commentary() -> None:
    md = (
        "# Title\n\n"
        "Final paragraph of the article.\n\n"
        "I hope this helps! Let me know if you'd like any changes."
    )
    cleaned = _clean_blog_markdown(md)
    assert cleaned.endswith("Final paragraph of the article.")
    assert "I hope this helps" not in cleaned
    assert "Let me know" not in cleaned


def test_clean_no_h1_keeps_first_substantive_line() -> None:
    md = "Just a flat paragraph response with no heading."
    cleaned = _clean_blog_markdown(md)
    assert cleaned == "Just a flat paragraph response with no heading."


def test_clean_strips_preamble_when_no_h1() -> None:
    md = "Here is the content:\n\n" "Just a flat paragraph response with no heading."
    cleaned = _clean_blog_markdown(md)
    assert cleaned == "Just a flat paragraph response with no heading."


def test_clean_handles_empty_input() -> None:
    assert _clean_blog_markdown("") == ""


def test_clean_handles_whitespace_only() -> None:
    # Empty after stripping; falls back to the stripped original.
    assert _clean_blog_markdown("   \n\n  \n") == ""


def test_clean_preserves_h1_at_start_with_blank_lines() -> None:
    md = "\n\n\n# Title\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned == "# Title\n\nBody."


# ---------------------------------------------------------------------------
# H1 title-normalisation tests
# ---------------------------------------------------------------------------


def test_clean_strips_double_asterisk_h1() -> None:
    md = "# **Decoder-only Diffusion**\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Decoder-only Diffusion")
    assert "**" not in cleaned.split("\n", 1)[0]


def test_clean_strips_escaped_asterisk_h1() -> None:
    md = "# \\*\\*Real Title\\*\\*\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Real Title"), cleaned


def test_clean_strips_underscore_h1() -> None:
    md = "# __Italics-style Title__\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Italics-style Title")


def test_clean_strips_single_asterisk_h1() -> None:
    md = "# *Italic Title*\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Italic Title")


def test_clean_strips_backtick_h1() -> None:
    md = "# `code-y title`\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# code-y title")


def test_clean_strips_quote_h1() -> None:
    md = '# "Quoted Title"\n\nBody.'
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Quoted Title")


def test_clean_handles_double_h1_marker() -> None:
    md = "# # Real Title\n\nBody."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Real Title")


def test_clean_does_not_strip_emphasis_inside_title() -> None:
    md = "# Some **bold** word in middle\n\nBody."
    cleaned = _clean_blog_markdown(md)
    # Inner emphasis is fine; only wrapping emphasis is stripped.
    assert cleaned.startswith("# Some **bold** word in middle")


def test_clean_only_normalises_first_h1() -> None:
    md = "# **Title**\n\nBody.\n\n## **Section**\n\nMore body."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Title")
    # H2 sections and below are untouched.
    assert "## **Section**" in cleaned


def test_normalise_title_idempotent() -> None:
    plain = "Real Title"
    assert _normalise_blog_title(plain) == plain
    assert _normalise_blog_title(_normalise_blog_title(plain)) == plain


def test_normalise_title_nested_wrappers() -> None:
    # Model wraps in **__title__** — strip both.
    assert _normalise_blog_title("**__Real Title__**") == "Real Title"
