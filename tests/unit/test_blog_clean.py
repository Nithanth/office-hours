"""Unit tests for `_clean_blog_markdown` post-processing.

Locks in the behaviour that strips chat-style preamble/postamble that LLMs
sometimes emit despite the prompt forbidding it (e.g. "Here is a compelling
blog post:" leaking into the H1).
"""

from __future__ import annotations

from api.routers.report_notes import _clean_blog_markdown


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
