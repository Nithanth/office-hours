"""Prompt sizing and token-budget helper functions.

This module converts qualitative size keys ("S/M/L/XL") into concrete
token- or character-budgets given the selected LLM’s context window. It is
**stateless** and therefore safe for import by any service without risk of
side-effects or circular dependencies.
"""

from typing import Final, cast

from config import config
from prompts.prompt_builder import AUDIENCE_PROMPT_MAP

# ---------------------------------------------------------------------------
# Tunable heuristics – shared across workflows
# ---------------------------------------------------------------------------

AVG_CHARS_PER_TOKEN: Final[int] = 4  # rough empirical value
PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS: Final[int] = 350
SAFETY_MARGIN_TOKENS: Final[int] = 150  # buffer against off-by-one / encoding quirks

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_output_token_target(
    audience_type: str,
    summary_size_key: str,
    *,
    is_topic_summary: bool = False,
) -> int:
    """Return the *desired* LLM output length in **tokens**.

    Falls back to the technical defaults if an audience / size combination
    is missing from config.SUMMARY_OUTPUT_TOKEN_TARGETS.
    """

    default_map = config.SUMMARY_OUTPUT_TOKEN_TARGETS.get("technical", {})

    # Topic summaries may use a different default fallback length (slightly larger)
    fallback_size_key = "L" if is_topic_summary else "M"
    fallback = default_map.get(fallback_size_key, 800 if is_topic_summary else 500)

    return cast(
        int,
        config.SUMMARY_OUTPUT_TOKEN_TARGETS.get(audience_type, {}).get(
            summary_size_key,
            fallback,
        ),
    )


def estimate_prompt_overhead(
    audience_type: str,
    *,
    is_topic_summary: bool = False,
) -> int:
    """Approximate **input** token cost of the static prompt template."""

    active_prompts = AUDIENCE_PROMPT_MAP.get(audience_type, AUDIENCE_PROMPT_MAP["technical"])
    template = active_prompts["topic"] if is_topic_summary else active_prompts["paper"]

    template_specific_tokens = len(template) // AVG_CHARS_PER_TOKEN
    return PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS + template_specific_tokens


def max_content_snippet_chars(
    *,
    selected_model_input_token_limit: int,
    audience_type: str,
    summary_size_key: str,
    is_topic_summary: bool = False,
) -> int:
    """Number of **characters** of source text we can safely inject.

    The calculation subtracts:
    1. Template overhead tokens.
    2. Intended LLM output tokens.
    3. A safety margin.
    Then converts the remaining token budget to characters.
    """

    output_tokens = get_output_token_target(
        audience_type,
        summary_size_key,
        is_topic_summary=is_topic_summary,
    )
    overhead_tokens = estimate_prompt_overhead(
        audience_type,
        is_topic_summary=is_topic_summary,
    )

    available_tokens = (
        selected_model_input_token_limit - overhead_tokens - output_tokens - SAFETY_MARGIN_TOKENS
    )

    if available_tokens <= 0:
        # Conservative fallback – sufficient to fit an abstract
        return 1000

    max_chars = available_tokens * AVG_CHARS_PER_TOKEN
    # Apply global hard cap from config
    return cast(int, min(max_chars, config.PAPER_MAX_CONTENT_SNIPPET_CHARS))


__all__ = [
    "get_output_token_target",
    "estimate_prompt_overhead",
    "max_content_snippet_chars",
]
