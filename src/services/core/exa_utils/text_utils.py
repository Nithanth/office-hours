"""Minimal helpers for whitespace-normalising text blocks."""

import re
import textwrap

from loguru import logger


def clean_text(raw: str | None) -> str:
    """Strip indentation, trim lines, collapse excess blank lines."""
    if not raw:
        return ""
    dedented = textwrap.dedent(raw)
    # Collapse >2 consecutive blank lines
    dedented = re.sub(r"\n{3,}", "\n\n", dedented)
    # Strip trailing whitespace per line
    dedented = "\n".join(line.rstrip() for line in dedented.splitlines())
    cleaned = dedented.strip()
    logger.debug("clean_text", in_len=len(raw or ""), out_len=len(cleaned))
    return cleaned
