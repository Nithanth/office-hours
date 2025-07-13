"""Caching helpers (stateful).

This sub-package groups the lightweight on-disk caches and UI-level result
containers used by the application.
"""

from .cached_search import CachedSearch  # noqa: F401
from .paper_cache import PaperCache  # noqa: F401
