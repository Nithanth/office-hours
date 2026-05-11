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


# ---------------------------------------------------------------------------
# `_build_blog_system_prompt` – user-tunable audience / length / tone / extra
# ---------------------------------------------------------------------------
from api.routers.report_notes import (  # noqa: E402  (import after assertions for visual grouping)
    _BLOG_SYSTEM_PROMPT,
    _build_blog_system_prompt,
)


def test_build_prompt_defaults_to_technical_medium_distill() -> None:
    out = _build_blog_system_prompt(audience=None, length=None, tone=None, extra=None)
    assert out.startswith(_BLOG_SYSTEM_PROMPT.rstrip()), "base prompt must come first"
    assert "**Audience.** Write for a **technical** reader" in out
    assert "**Length.** Target a **medium-length** post" in out
    assert "**Tone.** **Distill.pub** style" in out
    assert "Additional instructions from the user" not in out


def test_build_prompt_layperson_short_casual() -> None:
    out = _build_blog_system_prompt(
        audience="layperson", length="short", tone="casual", extra=None
    )
    assert "**Audience.** Write for a **layperson**" in out
    assert "**Length.** Target a **short** post" in out
    assert "**Tone.** **Casual blog** style" in out


def test_build_prompt_unknown_values_fall_back_to_defaults() -> None:
    out = _build_blog_system_prompt(
        audience="space-alien", length="enormous", tone="haiku", extra=None
    )
    # Unknown values silently fall back to the defaults — never raise.
    assert "**Audience.** Write for a **technical** reader" in out
    assert "**Length.** Target a **medium-length** post" in out
    assert "**Tone.** **Distill.pub** style" in out


def test_build_prompt_extra_is_blockquoted_and_preserved() -> None:
    extra = "Emphasise reproducibility.\nKeep diagrams minimal."
    out = _build_blog_system_prompt(
        audience="technical", length="medium", tone="distill", extra=extra
    )
    assert "Additional instructions from the user" in out
    assert "> Emphasise reproducibility." in out
    assert "> Keep diagrams minimal." in out


def test_build_prompt_extra_is_truncated_at_max_chars() -> None:
    long_extra = "A" * 800
    out = _build_blog_system_prompt(
        audience="technical", length="medium", tone="distill", extra=long_extra
    )
    # The block-quoted extra section must contain at most 500 'A's.
    assert "> " + ("A" * 500) in out
    assert "> " + ("A" * 501) not in out


def test_build_prompt_blank_extra_omits_section() -> None:
    out = _build_blog_system_prompt(
        audience="technical", length="medium", tone="distill", extra="   \n  "
    )
    assert "Additional instructions from the user" not in out


def test_build_prompt_case_insensitive_keys() -> None:
    out = _build_blog_system_prompt(
        audience="LAYPERSON", length="LONG", tone="LECTURE", extra=None
    )
    assert "**Audience.** Write for a **layperson**" in out
    assert "**Length.** Target a **long** post" in out
    assert "**Tone.** **Lecture-notes** style" in out


# ---------------------------------------------------------------------------
# Bare-bold title promotion (Fix #1: title asterisks regression)
# ---------------------------------------------------------------------------
# When the model emits the title as `**Title**` instead of `# Title`, the
# old extractTitle picked it up with the literal asterisks. _clean_blog_markdown
# now promotes a leading bare-emphasis line to a proper H1 so the page renders
# clean.


def test_promote_bare_bold_title_to_h1() -> None:
    md = (
        "**Why Transformers Won the Race**\n\nIn 2017 the original Transformer paper..."
    )
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Why Transformers Won the Race")
    assert "**Why Transformers" not in cleaned.splitlines()[0]


def test_promote_bare_underscore_title_to_h1() -> None:
    md = "__Calibrating Diffusion Models__\n\nA new study..."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Calibrating Diffusion Models")


def test_promote_bare_italic_title_to_h1() -> None:
    md = "*A Quick Tour of MoE Routing*\n\nMixture-of-Experts..."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# A Quick Tour of MoE Routing")


def test_promote_escaped_bold_title_to_h1() -> None:
    # Some models escape the asterisks (`\*\*Title\*\*`); we should still strip.
    md = r"\*\*Beyond Scaling Laws\*\*" + "\n\nThe field has matured..."
    cleaned = _clean_blog_markdown(md)
    assert cleaned.startswith("# Beyond Scaling Laws"), cleaned[:80]


def test_promote_skips_paragraph_with_emphasis() -> None:
    # Long lines that happen to start with emphasis aren't titles — they're
    # body paragraphs (>120 char threshold). Leave them alone.
    long_line = "**Note:** This is a long paragraph that opens with bold text " * 4
    md = long_line + "\n\nMore body."
    cleaned = _clean_blog_markdown(md)
    # No H1 should be invented from a body paragraph.
    assert not cleaned.startswith("# Note:"), cleaned[:120]


def test_promote_handles_existing_h1_unchanged() -> None:
    md = "# Already An H1\n\n**Bold paragraph that should not become H1.**\n\nBody."
    cleaned = _clean_blog_markdown(md)
    # The H1 stays; the bold paragraph is body and should remain bold.
    assert cleaned.startswith("# Already An H1")
    assert "**Bold paragraph" in cleaned


# ---------------------------------------------------------------------------
# Per-length diagram budget (Fix #4: prompt updates)
# ---------------------------------------------------------------------------


def test_diagram_budget_short_length() -> None:
    from api.routers.report_notes import _build_blog_system_prompt

    out = _build_blog_system_prompt(
        audience="technical", length="short", tone="distill", extra=None
    )
    assert "SHORT post" in out
    assert "at least one diagram" in out


def test_diagram_budget_medium_length() -> None:
    from api.routers.report_notes import _build_blog_system_prompt

    out = _build_blog_system_prompt(
        audience="technical", length="medium", tone="distill", extra=None
    )
    assert "MEDIUM post" in out
    assert "at least 1 and ideally 2 diagrams" in out


def test_diagram_budget_long_length() -> None:
    from api.routers.report_notes import _build_blog_system_prompt

    out = _build_blog_system_prompt(
        audience="technical", length="long", tone="distill", extra=None
    )
    assert "LONG post" in out
    assert "at least 2 and ideally 3 diagrams" in out


def test_diagram_budget_unknown_length_falls_back_to_default() -> None:
    from api.routers.report_notes import _build_blog_system_prompt

    out = _build_blog_system_prompt(
        audience="technical", length="absurdly-long", tone="distill", extra=None
    )
    # Unknown length falls back to the medium default in _DEFAULT_BLOG_OPTS.
    assert "MEDIUM post" in out
