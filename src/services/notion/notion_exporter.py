"""Export a Lux Scholar report to Notion as a page or database row."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from loguru import logger

from services.notion.markdown_to_blocks import chunk_blocks, markdown_to_blocks


class NotionExportError(RuntimeError):
    """Raised when a Notion export cannot complete."""


class NotionExporter:
    """Thin wrapper over the official ``notion-client`` async SDK."""

    def __init__(self, api_key: str) -> None:
        try:
            from notion_client import AsyncClient
        except ImportError as err:
            raise NotionExportError(
                "notion-client is not installed. Add it to requirements.txt."
            ) from err
        self._client = AsyncClient(auth=api_key)

    @staticmethod
    def _create_error_message(err: Exception, target_kind: str) -> str:
        """Return a concise, actionable Notion API error."""

        status = getattr(err, "status", None)
        code = getattr(err, "code", None)
        message = getattr(err, "message", None) or str(err)

        if status == 404 or code == "object_not_found":
            return (
                f"Notion could not find the configured {target_kind}. "
                "Verify the ID in .env and share that page/database with your Notion integration."
            )
        if status == 401 or code == "unauthorized":
            return "Notion rejected NOTION_API_KEY. Verify the integration token in .env."
        if status == 400 or code == "validation_error":
            return (
                "Notion rejected the export payload. If exporting to a database, verify its "
                "properties match the expected schema or use NOTION_PARENT_PAGE_ID instead."
            )
        return f"Notion page creation failed: {message}"

    async def export_report(
        self,
        *,
        title: str,
        markdown: str,
        parent_page_id: str | None,
        parent_database_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a Notion page for *markdown* and return its URL.

        If ``parent_database_id`` is supplied the page becomes a database row
        with typed properties derived from ``metadata``. Otherwise it is a
        child page under ``parent_page_id``.
        """
        if not parent_database_id and not parent_page_id:
            raise NotionExportError("Configure NOTION_PARENT_PAGE_ID or NOTION_PARENT_DATABASE_ID.")

        blocks = markdown_to_blocks(markdown)
        batches = chunk_blocks(blocks)
        first_batch = batches[0] if batches else []

        if parent_database_id:
            parent = {"database_id": parent_database_id}
            properties = _database_properties(title, metadata or {})
            target_kind = "database"
        else:
            if parent_page_id is None:
                raise NotionExportError("Configure NOTION_PARENT_PAGE_ID.")
            parent = {"page_id": parent_page_id}
            properties = _page_title_property(title)
            target_kind = "page"

        try:
            page = await self._client.pages.create(
                parent=parent, properties=properties, children=first_batch
            )
        except Exception as err:  # notion_client raises APIResponseError subclasses
            logger.error(
                "notion_create_failed",
                error=str(err),
                status=getattr(err, "status", None),
                code=getattr(err, "code", None),
            )
            raise NotionExportError(self._create_error_message(err, target_kind)) from err

        page_id = page["id"]

        # Append remaining block batches.
        for batch in batches[1:]:
            try:
                await self._client.blocks.children.append(block_id=page_id, children=batch)
            except Exception as err:
                logger.warning("notion_append_failed", error=str(err))
                # Partial export is still useful; don't fail the whole export.
                break

        return page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"

    async def close(self) -> None:
        await self._client.aclose()


def _page_title_property(title: str) -> dict[str, Any]:
    return {
        "title": {
            "title": [{"type": "text", "text": {"content": title[:200]}}],
        }
    }


def _database_properties(title: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Build properties for a database row.

    Unknown columns are silently dropped by the Notion API only if the schema
    matches; callers should ensure the database has these properties or extend
    this mapping.
    """
    props: dict[str, Any] = {
        "Name": {"title": [{"type": "text", "text": {"content": title[:200]}}]},
    }

    if url := metadata.get("source_url"):
        props["Source URL"] = {"url": url}
    if audience := metadata.get("audience"):
        props["Audience"] = {"select": {"name": audience}}
    if model := metadata.get("model"):
        props["Model"] = {"rich_text": [{"type": "text", "text": {"content": model[:200]}}]}
    if authors := metadata.get("authors"):
        joined = ", ".join(authors) if isinstance(authors, list) else str(authors)
        props["Authors"] = {"rich_text": [{"type": "text", "text": {"content": joined[:2000]}}]}
    if year := metadata.get("year"):
        with suppress(TypeError, ValueError):
            props["Year"] = {"number": int(year)}
    if export_type := metadata.get("type"):
        props["Type"] = {"select": {"name": export_type}}
    if tags := metadata.get("tags"):
        tag_list = tags if isinstance(tags, list) else [tags]
        props["Tags"] = {"multi_select": [{"name": t.strip()[:100]} for t in tag_list if t.strip()]}

    return props
