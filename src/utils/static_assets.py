"""Versioned static-asset URLs.

Stylesheets/scripts were served with heuristic caching (Last-Modified/ETag
only, no explicit Cache-Control), so returning users could see stale CSS
until the browser revalidated. `static_url()` appends `?v=<file mtime>` —
the URL changes whenever the file changes, so deploys bust the cache and
unchanged assets stay cacheable forever.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache

_STATIC_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@lru_cache(maxsize=512)
def static_url(path: str) -> str:
    """Return *path* (e.g. ``/static/css/base.css``) with a ``?v=`` content tag.

    The tag is a short content hash, not mtime — two checkouts in the same
    second can share an mtime and would silently serve the old body under
    an unchanged URL (browsers cache ES modules aggressively).
    """
    try:
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/") :]
        fs_path = os.path.join(_STATIC_ROOT, rel)
        with open(fs_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:10]
    except OSError:
        return path
    return f"{path}?v={digest}"
