from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from api.deps import get_exa_client
from services.core.exa_client import ExaClient

router = APIRouter()


@router.get("/metadata")
async def get_metadata(
    url: str = Query(..., description="Document URL or Exa document ID"),
    exa_client: Annotated[ExaClient | None, Depends(get_exa_client)] = None,
) -> dict[str, Any]:
    """Return the cached metadata entry (or fetch + cache it on first request)."""
    if exa_client is None:
        raise HTTPException(status_code=503, detail="Exa client unavailable")

    # Accept URLs accidentally wrapped in Markdown-style brackets like "[https://…]"
    clean_url = url.strip().lstrip("[").rstrip("]")
    cache = exa_client._cache  # pyright: ignore[reportPrivateUsage]
    record: dict[str, Any] | None = cache.get(clean_url)

    if record is None:
        # Populate cache on first access
        try:
            logger.debug(
                "metadata_endpoint cache miss - running enrich_url", url=clean_url
            )
            exa_client.enrich_url(clean_url)
            record = cache.get(clean_url)
        except Exception as exc:  # noqa: BLE001
            # Surface enrichment failure to caller so they know why 502
            raise HTTPException(
                status_code=502, detail=f"Enrichment failed: {exc}"
            ) from exc

    if record is None:
        raise HTTPException(status_code=404, detail="No metadata found for url")

    # At this point `record` is guaranteed not None
    return cast(dict[str, Any], record)
