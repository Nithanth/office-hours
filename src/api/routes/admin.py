"""Health-check and small admin utilities (cache flush, metadata recheck)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger

import config as _cfg
from api.deps import get_exa_client
from services.report.report_utils import SummaryCache

_admin_security = HTTPBearer(auto_error=False)

# Router instance for admin endpoints
router = APIRouter()


def _require_admin(creds: HTTPAuthorizationCredentials | None) -> None:
    """Fail closed for admin operations in production."""

    secret = _cfg.config.ADMIN_API_KEY
    if not secret:
        if _cfg.config.ENV == "prod":
            logger.error("admin_secret_missing")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Admin API key is not configured",
            )
        return

    if creds is None or creds.credentials != secret:
        logger.warning("admin_forbidden")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/healthz")
async def healthz() -> dict[str, bool]:
    """Liveness probe consumed by orchestration platforms."""
    return {"ok": True}


@router.get("/admin/flush-cache", tags=["admin"])
async def flush_summary_cache(  # noqa: B008 - Depends used as default per FastAPI pattern
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_security),  # noqa: B008
) -> dict[str, bool]:
    """Clear all cached LLM summaries."""
    _require_admin(creds)

    SummaryCache.clear()
    logger.info("summary_cache_flushed")
    return {"ok": True}


@router.post("/admin/recheck-metadata", tags=["admin"])
async def recheck_paper_metadata(
    limit: int = 100,
    creds: HTTPAuthorizationCredentials | None = Depends(_admin_security),  # noqa: B008
) -> dict[str, int]:
    """Manually trigger a batch re-enrichment of incomplete paper metadata."""

    _require_admin(creds)

    exa_client = get_exa_client()
    if exa_client is None:
        logger.error("exa_client_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exa client not ready",
        )

    processed = exa_client.recheck_incomplete_metadata(limit=limit)
    logger.info("metadata_recheck_complete", processed=processed)
    return {"processed": processed}


@router.get("/api/notion-status", tags=["admin"])
async def notion_status(request: Request) -> dict[str, Any]:
    """Return Notion integration connection status (no secrets exposed)."""
    from services.auth.session import get_auth_user_id
    from services.persistence.database import database_available, session_scope

    user_id = get_auth_user_id(request)
    if user_id and database_available():
        from services.persistence.repositories import get_notion_integration

        async for session in session_scope():
            integration = await get_notion_integration(session, user_id=user_id)
        if integration is not None:
            target_id = integration.parent_database_id or integration.parent_page_id or ""
            masked_id = f"...{target_id[-8:]}" if len(target_id) > 8 else target_id
            return {
                "connected": True,
                "oauth_connected": True,
                "database_id": masked_id,
                "has_api_key": True,
                "has_parent": bool(target_id),
                "target_type": "database"
                if integration.parent_database_id
                else ("page" if integration.parent_page_id else "none"),
                "workspace_name": integration.workspace_name,
            }

    has_key = bool(_cfg.config.NOTION_API_KEY)
    db_id = _cfg.config.NOTION_PARENT_DATABASE_ID or ""
    page_id = _cfg.config.NOTION_PARENT_PAGE_ID or ""
    has_parent = bool(db_id or page_id)

    # Mask the database/page ID — show only last 8 chars
    target_id = db_id or page_id
    masked_id = f"...{target_id[-8:]}" if len(target_id) > 8 else target_id

    return {
        "connected": has_key and has_parent,
        "oauth_connected": False,
        "database_id": masked_id if has_parent else "",
        "has_api_key": has_key,
        "has_parent": has_parent,
        "target_type": "database" if db_id else ("page" if page_id else "none"),
    }
