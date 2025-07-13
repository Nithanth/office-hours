"""Search utilities."""

import re
from typing import Final

# Pre-compiled regex that recognises common conversational prefixes a user might
# prepend to a query (e.g. "explain to me", "what is") which usually hurt
# search recall.
_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:explain(?: to me)?|describe|tell me(?: about)?|can you explain|what is|how does|"
    r"how do|how to|provide an overview of)\s+",
    re.IGNORECASE,
)


def clean_query(query: str) -> str:
    """Return a cleaned-up version of *query* ready for Exa search."""

    query = query.strip()
    return _PREFIX_PATTERN.sub("", query)
