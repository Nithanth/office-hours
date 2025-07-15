"""Caching helpers (stateful).

This sub-package groups the lightweight on-disk caches and UI-level result
containers used by the application.
"""

# Re-export primary cache container class
from .cached_search import CachedSearch as CachedSearch

__all__: list[str] = ["CachedSearch"]
