"""Centralised prompt template loader.

This module exposes `load_template(template_type, audience)` which returns
an LLM prompt template string while allowing two layers of flexibility:

1. Developers (or even end-users in a deployed setting) can drop a file at
   `lux_scholar/prompts/<template_type>/<audience>.md` to override a prompt
   without touching any Python code.
2. If no such file exists the function gracefully falls back to the original
   constants that still live on the `config` object, so existing behaviour is
   preserved until we physically migrate those strings into markdown files.

Template directory layout (optional):
    prompts/
        topic/
            technical.md
            semi_technical.md
            non_technical.md
        paper/
            technical.md
            semi_technical.md
            non_technical.md

Additional template groups (e.g. "direct_query") can be added using the same
pattern — just create the folder and corresponding audience files.
"""

from __future__ import annotations

from pathlib import Path

from config import config  # existing source-of-truth for legacy strings

# ---------------------------------------------------------------------------
# Fallbacks – bridge to existing in-code constants so nothing breaks
# ---------------------------------------------------------------------------
_FALLBACKS: dict[tuple[str, str], str] = {
    ("topic", "technical"): config.TOPIC_SUMMARY_PROMPT_TEMPLATE_TECHNICAL,
    ("topic", "semi_technical"): config.TOPIC_SUMMARY_PROMPT_SEMI_TECH,
    ("topic", "non_technical"): config.TOPIC_SUMMARY_PROMPT_NON_TECH,
    ("paper", "technical"): config.PAPER_SUMMARY_PROMPT_TEMPLATE_TECHNICAL,
    ("paper", "semi_technical"): config.PAPER_SUMMARY_PROMPT_SEMI_TECH,
    ("paper", "non_technical"): config.PAPER_SUMMARY_PROMPT_NON_TECH,
}

# Base directory where prompt override files may live — same folder as this __init__
_BASE_DIR = Path(__file__).parent


def load_template(template_type: str, audience: str) -> str:  # noqa: D401 – simple loader
    """Return the prompt template for (*template_type*, *audience*).

    Template search order:
        1. `prompts/<template_type>/<audience>.md` (override file)
        2. Hard-coded constant on the `config` singleton (legacy fallback)
    """
    file_path = _BASE_DIR / template_type / f"{audience}.md"
    if file_path.exists():
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 – fall back if file unreadable
            pass

    # Fallback – guaranteed to exist given the previous codebase state
    return _FALLBACKS[(template_type, audience)]
