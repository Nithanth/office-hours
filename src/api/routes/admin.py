"""Health-check and small admin utilities (cache flush, metadata recheck)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

import config as _cfg
from api.deps import get_exa_client
from services.report.report_utils import SummaryCache

_admin_security = HTTPBearer(auto_error=False)

# Router instance for admin endpoints
router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    """Liveness probe consumed by orchestration platforms."""
    return {"ok": True}


@router.get("/admin/flush-cache", tags=["admin"])
async def flush_summary_cache(  # noqa: B008 - Depends used as default per FastAPI pattern
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_security),  # noqa: B008
) -> dict[str, bool]:
    """Clear all cached LLM summaries."""
    secret = _cfg.config.ADMIN_API_KEY
    if secret and (creds is None or creds.credentials != secret):
        logger.warning("flush_cache_forbidden")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    SummaryCache.clear()
    logger.info("summary_cache_flushed")
    return {"ok": True}


@router.post("/admin/recheck-metadata", tags=["admin"])
async def recheck_paper_metadata(
    limit: int = 100,
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_security),  # noqa: B008
) -> dict[str, int]:
    """Manually trigger a batch re-enrichment of incomplete paper metadata."""

    secret = _cfg.config.ADMIN_API_KEY
    if secret and (creds is None or creds.credentials != secret):
        logger.warning("metadata_recheck_forbidden")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    exa_client = get_exa_client()
    if exa_client is None:
        logger.error("exa_client_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Exa client not ready"
        )

    processed = exa_client.recheck_incomplete_metadata(limit=limit)
    logger.info("metadata_recheck_complete", processed=processed)
    return {"processed": processed}


@router.get("/api/notion-status", tags=["admin"])
async def notion_status() -> dict[str, Any]:
    """Return Notion integration connection status (no secrets exposed)."""
    has_key = bool(_cfg.config.NOTION_API_KEY)
    db_id = _cfg.config.NOTION_PARENT_DATABASE_ID or ""
    page_id = _cfg.config.NOTION_PARENT_PAGE_ID or ""
    has_parent = bool(db_id or page_id)

    # Mask the database/page ID — show only last 8 chars
    target_id = db_id or page_id
    masked_id = f"...{target_id[-8:]}" if len(target_id) > 8 else target_id

    return {
        "connected": has_key and has_parent,
        "database_id": masked_id if has_parent else "",
        "has_api_key": has_key,
        "has_parent": has_parent,
        "target_type": "database" if db_id else ("page" if page_id else "none"),
    }
