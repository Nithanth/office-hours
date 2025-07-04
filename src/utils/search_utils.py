"""utils/search_utils.py

Utility functions for search-related helpers that need to be shared across
modules (frontend handlers, service code, etc.).  Centralising them here keeps
`app.py` slimmer and avoids accidental duplicate implementations.
"""

from __future__ import annotations

import re
from typing import Final

# Pre-compiled regex that recognises common conversational prefixes a user might
# prepend to a query (e.g. "explain to me", "what is") which usually hurt
# search recall.  The pattern is intentionally conservative – we only trim when
# we are reasonably sure it is a polite preamble and not part of the subject
# matter.
_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:explain(?: to me)?|describe|tell me(?: about)?|can you explain|what is|how does|"
    r"how do|how to|provide an overview of)\s+",
    re.IGNORECASE,
)

__all__ = ["clean_query"]


def clean_query(query: str) -> str:
    """Return a cleaned-up version of *query* ready for Exa search.

    1. Strips leading/trailing whitespace.
    2. Removes polite/filler prefixes captured by :pydata:`_PREFIX_PATTERN`.
    """

    query = query.strip()
    return _PREFIX_PATTERN.sub("", query)
