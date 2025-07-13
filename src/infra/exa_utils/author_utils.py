"""Lightweight heuristics for detecting plausible author names."""

import re
from loguru import logger

# Require at least one capital followed by lowercase
_NAME_PATTERN = re.compile(r"[A-Z][a-z].*")


def is_plausible_author(token: str) -> bool:
    """Return **True** iff *token* resembles a real person's name."""
    t = token.strip()
    if len(t) < 2:
        return False
    # Strip trailing punctuation
    t = t.rstrip(",;*")
    # Skip pure lowercase/username-style strings (e.g., "konasj")
    if t == t.lower() and " " not in t:
        return False
    # Reject tokens containing characters other than letters, dots, spaces or hyphens
    if re.search(r"[^A-Za-z.\-\s]", t):
        return False
    # Require at least one capitalised segment
    if not _NAME_PATTERN.search(t):
        return False
    # Exclude tokens starting with [ or mentioning submission/revision
    if t.startswith("["):
        return False
    if any(kw in t.lower() for kw in ("submitted", "revised")):
        return False
    # If no whitespace present, treat as initials only when very short (≤3)
    if " " not in t and "." not in t:
        short_suffixes = {"jr", "sr", "iii", "iv"}
        if len(t) > 3 and t.lower() not in short_suffixes:
            return False
    result = True
    logger.debug("author_token_evaluated", token=token, plausible=result)
    return result


def filter_plausible_authors(authors: list[str] | None) -> list[str]:
    """Filter *authors* down to plausible names only."""
    if not authors:
        return []
    filtered = [a for a in authors if is_plausible_author(a)]
    logger.debug("authors_filtered", total=len(authors), kept=len(filtered))
    return filtered
