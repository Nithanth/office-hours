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

from pathlib import Path

# Prompt templates now live exclusively in markdown files under ``src/prompts``.
# If a requested template file is missing we raise an explicit error so the
# issue surfaces during development.

# Base directory where prompt override files may live — same folder as this __init__
_BASE_DIR = Path(__file__).parent


def load_template(
    template_type: str, audience: str
) -> str:  # noqa: D401 – simple loader
    """Return the prompt template for (*template_type*, *audience*).

    Template location: `prompts/<template_type>/<audience>.md`.
    The function raises ``FileNotFoundError`` if the file is absent.
    """
    file_path = _BASE_DIR / template_type / f"{audience}.md"
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Prompt template not found: {file_path.relative_to(_BASE_DIR.parent)}"
        ) from exc
