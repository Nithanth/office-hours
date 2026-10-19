"""Unpaywall open-access resolution.

Resolves a DOI to the paper's best open-access location via the Unpaywall
API — the next canonicalization layer after arXiv for papers whose only
open copy lives in a repository, PMC, or a publisher's OA mirror.

API: https://unpaywall.org/products/api (free; requires a contact email —
reuses the OPENALEX_MAILTO polite-pool convention).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional
from urllib.error import HTTPError, URLError

from loguru import logger

import config as _cfg

_UPW_BASE = "https://api.unpaywall.org/v2"


def _mailto() -> str | None:
    return getattr(_cfg.config, "OPENALEX_MAILTO", None)


def resolve_open_access_url(doi: str, *, timeout: int = 6) -> Optional[str]:
    """Return the best open-access URL for *doi*, or None.

    Prefers a direct PDF (``url_for_pdf``), then the OA landing page.
    Returns None when Unpaywall is unreachable, the mailto contact isn't
    configured, or the paper is closed access everywhere it tracks.
    """
    email = _mailto()
    if not email or not doi:
        return None

    quoted = urllib.parse.quote(doi, safe="")
    url = f"{_UPW_BASE}/{quoted}?email={urllib.parse.quote(email)}"
    req = urllib.request.Request(url, headers={"User-Agent": "office-hours/0.1"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (URLError, HTTPError, json.JSONDecodeError, TimeoutError) as exc:
        logger.debug("unpaywall_fetch_failed", doi=doi, err=str(exc))
        return None

    if not data.get("is_oa"):
        return None

    best = data.get("best_oa_location") or {}
    for key in ("url_for_pdf", "url", "url_for_landing_page"):
        loc = best.get(key)
        if loc and str(loc).startswith("http"):
            return str(loc)

    # best_oa_location empty but other OA locations exist — rare, cheap to try
    for loc in data.get("oa_locations") or []:
        for key in ("url_for_pdf", "url", "url_for_landing_page"):
            candidate = loc.get(key)
            if candidate and str(candidate).startswith("http"):
                return str(candidate)

    return None
