"""Admin and health-check endpoints extracted from legacy ``app.py``."""

from __future__ import annotations

import os

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from utils.summary_cache import SummaryCache

logger = structlog.get_logger()

router = APIRouter()

_admin_security = HTTPBearer(auto_error=False)


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    """Simple liveness probe for deployment platforms."""
    return {"ok": True}


@router.get("/admin/flush-cache", tags=["admin"])
async def flush_summary_cache(  # noqa: B008 – Depends used as default per FastAPI pattern
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_security),  # noqa: B008
) -> dict[str, bool]:
    """Clear all cached LLM summaries.

    Requires an ``Authorization: Bearer <ADMIN_API_KEY>`` header when the
    ``ADMIN_API_KEY`` environment variable is set. If unset the route
    remains open (dev convenience).
    """
    secret = os.getenv("ADMIN_API_KEY")
    if secret and (creds is None or creds.credentials != secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    SummaryCache.clear()
    return {"ok": True}
