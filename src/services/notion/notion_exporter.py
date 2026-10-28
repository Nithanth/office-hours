"""Export an Office Hours report to Notion as a page or database row."""

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
        self._client = AsyncClient(
            options={"auth": api_key, "notion_version": "2022-06-28"}
        )

    @staticmethod
    def _create_error_message(err: Exception, target_kind: str) -> str:
        """Return a concise, actionable Notion API error."""

        status = getattr(err, "status", None)
        code = getattr(err, "code", None)
        message = getattr(err, "message", None) or str(err)

        if status == 404 or code == "object_not_found":
            return (
                f"Notion could not find the configured {target_kind} — it may "
                "have been deleted. Pick the destination again from the library page."
            )
        if status == 401 or code == "unauthorized":
            return (
                "Notion rejected the connection — disconnect and reconnect "
                "Notion from Settings to re-authorize."
            )
        if status == 400 or code == "validation_error":
            return (
                "Notion rejected the export payload. If syncing to a database, "
                "its columns may have been edited — re-pick the destination from "
                "the library page to provision a fresh one."
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
            raise NotionExportError(
                "Configure NOTION_PARENT_PAGE_ID or NOTION_PARENT_DATABASE_ID."
            )

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
            raise NotionExportError(
                self._create_error_message(err, target_kind)
            ) from err

        page_id = page["id"]

        # Append remaining block batches.
        for batch in batches[1:]:
            try:
                await self._client.blocks.children.append(
                    block_id=page_id, children=batch
                )
            except Exception as err:
                logger.warning("notion_append_failed", error=str(err))
                # Partial export is still useful; don't fail the whole export.
                break

        return page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"

    async def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        """Append pre-built blocks to a page in <=100-block batches."""
        for batch in chunk_blocks(blocks):
            try:
                await self._client.blocks.children.append(
                    block_id=page_id, children=batch
                )
            except Exception as err:
                logger.warning("notion_append_failed", error=str(err))
                break

    async def ensure_page_content(self, page_id: str, markdown: str) -> bool:
        """Append *markdown* as blocks to a page that has no children yet.

        Returns True when content was written. Existing content is left
        untouched so re-syncs never duplicate the page body.
        """
        if not await self.page_is_empty(page_id):
            return False
        await self.append_blocks(page_id, markdown_to_blocks(markdown))
        return True

    async def page_is_empty(self, page_id: str) -> bool:
        """True when the page body has no children — lets callers decide
        whether to fill it without paying for content generation first."""
        children = await self._client.blocks.children.list(block_id=page_id)
        return not children.get("results")

    async def replace_page_content(self, page_id: str, markdown: str) -> None:
        """Replace a page's whole body with *markdown* — archives every
        existing child block, then appends the new content. Used by the
        topic mirror where blog/notes are living documents that change
        between syncs (unlike one-shot report exports)."""
        cursor = None
        while True:
            resp = await self._client.blocks.children.list(
                block_id=page_id, start_cursor=cursor
            )
            for child in resp.get("results", []):
                try:
                    await self._client.request(
                        path=f"blocks/{child['id']}", method="DELETE"
                    )
                except Exception as err:  # noqa: BLE001
                    logger.warning(
                        "notion_block_delete_failed",
                        block_id=child.get("id"),
                        error=str(err),
                    )
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        await self.append_blocks(page_id, markdown_to_blocks(markdown))

    async def archive_page(self, page_id: str) -> None:
        """Archive a page — used when a saved paper is removed from the
        library so its Notion row disappears too."""
        await self._client.request(
            path=f"pages/{page_id}",
            method="PATCH",
            body={"archived": True},
        )

    # ------------------------------------------------------------------
    # Library sync: relational Papers ↔ Topics databases
    # ------------------------------------------------------------------

    async def get_database_parent_page(self, database_id: str) -> str | None:
        """Return the page id that owns *database_id* (databases live in pages)."""
        db = await self.get_database(database_id)
        parent = db.get("parent") or {}
        if parent.get("type") == "page_id":
            return str(parent["page_id"])
        return None

    async def get_database(self, database_id: str) -> dict[str, Any]:
        """Retrieve a database object, raising NotionExportError on failure."""
        try:
            db = await self._client.databases.retrieve(database_id=database_id)
            return dict(db)
        except Exception as err:
            raise NotionExportError(
                self._create_error_message(err, "database")
            ) from err

    async def database_exists(self, database_id: str) -> bool:
        """True when the database is still reachable — False once the user
        deletes it in their workspace."""
        try:
            await self._client.databases.retrieve(database_id=database_id)
            return True
        except Exception:
            return False

    async def query_database_rows(
        self, database_id: str, *, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """All rows in a database (id + properties), for adopting rows left
        behind by a reset or a previous connection instead of duplicating."""
        rows: list[dict[str, Any]] = []
        cursor = None
        try:
            for _ in range(20):  # 20 * 100 = 2000-row cap
                body: dict[str, Any] = {"page_size": page_size}
                if cursor:
                    body["start_cursor"] = cursor
                resp = await self._client.request(
                    path=f"databases/{database_id}/query",
                    method="POST",
                    body=body,
                )
                rows.extend(resp.get("results") or [])
                if not resp.get("has_more"):
                    break
                cursor = resp.get("next_cursor")
        except Exception as err:
            logger.warning("notion_db_query_failed", error=str(err))
        return rows

    async def create_database(
        self, parent_page_id: str, title: str, properties: dict[str, Any]
    ) -> str:
        """Create a database under *parent_page_id*; return its id."""
        # The SDK's databases.create only forwards new-API fields
        # (initial_data_source), silently dropping `properties` — so we
        # call the endpoint directly with the classic 2022-06-28 body.
        try:
            db = await self._client.request(
                path="databases",
                method="POST",
                body={
                    "parent": {"type": "page_id", "page_id": parent_page_id},
                    "title": [{"type": "text", "text": {"content": title}}],
                    "properties": properties,
                },
            )
        except Exception as err:
            logger.error("notion_db_create_failed", error=str(err))
            raise NotionExportError(
                self._create_error_message(err, "database")
            ) from err
        return str(db["id"])

    async def add_property(
        self, database_id: str, name: str, prop: dict[str, Any]
    ) -> None:
        """Add a single property to a database schema."""
        try:
            await self._client.request(
                path=f"databases/{database_id}",
                method="PATCH",
                body={"properties": {name: prop}},
            )
        except Exception as err:
            raise NotionExportError(
                self._create_error_message(err, "database")
            ) from err

    async def add_relation_property(
        self, database_id: str, name: str, target_database_id: str
    ) -> None:
        """Add a two-way relation property to a database schema."""
        # Same SDK pick() limitation as create_database — direct request.
        try:
            await self._client.request(
                path=f"databases/{database_id}",
                method="PATCH",
                body={
                    "properties": {
                        name: {
                            "relation": {
                                "database_id": target_database_id,
                                "dual_property": {"synced_property_name": "Papers"},
                            }
                        }
                    }
                },
            )
        except Exception as err:
            logger.error("notion_relation_add_failed", error=str(err))
            raise NotionExportError(
                self._create_error_message(err, "database")
            ) from err

    async def get_page_status(self, page_id: str) -> str | None:
        """Read the 'Status' select value of a page, or None."""
        try:
            page = await self._client.pages.retrieve(page_id=page_id)
        except Exception:
            return None
        prop = (page.get("properties") or {}).get("Status") or {}
        sel = prop.get("select") or {}
        return sel.get("name")

    # ------------------------------------------------------------------
    # Comments (Q&A loop)
    # ------------------------------------------------------------------

    async def list_comments(self, block_id: str) -> list[dict[str, Any]]:
        """All comments on a block (page), flattened across discussions."""
        try:
            resp = await self._client.comments.list(block_id=block_id)
        except Exception as err:
            raise NotionExportError(self._create_error_message(err, "page")) from err
        return list(resp.get("results") or [])

    async def reply_to_discussion(self, discussion_id: str, text: str) -> None:
        """Post a reply in an existing comment thread.

        *text* is Markdown — converted to rich_text (bold/italic/code/links
        and inline equations). Falls back to plain text if Notion rejects
        the structured payload.
        """
        from services.notion.markdown_to_blocks import markdown_to_rich_text

        rich_text = markdown_to_rich_text(text[:1900])
        if not rich_text:
            rich_text = [{"type": "text", "text": {"content": text[:1900]}}]
        try:
            await self._client.comments.create(
                discussion_id=discussion_id,
                rich_text=rich_text,
            )
        except Exception as err:
            # Retry once as plain text — e.g. equation segments rejected.
            try:
                await self._client.comments.create(
                    discussion_id=discussion_id,
                    rich_text=[{"type": "text", "text": {"content": text[:1900]}}],
                )
            except Exception as err2:
                raise NotionExportError(
                    self._create_error_message(err2 or err, "comment thread")
                ) from err2

    async def create_database_row(
        self, database_id: str, properties: dict[str, Any]
    ) -> str | None:
        """Create a row in *database_id* and return its page id."""
        try:
            page = await self._client.pages.create(
                parent={"database_id": database_id}, properties=properties
            )
        except Exception as err:
            logger.error("notion_row_create_failed", error=str(err))
            raise NotionExportError(
                self._create_error_message(err, "database")
            ) from err
        page_id = page.get("id")
        return str(page_id) if page_id else None

    async def update_page_properties(
        self, page_id: str, properties: dict[str, Any]
    ) -> None:
        """Update the properties of an existing page/database row."""
        try:
            await self._client.pages.update(page_id=page_id, properties=properties)
        except Exception as err:
            logger.error("notion_row_update_failed", error=str(err))
            raise NotionExportError(self._create_error_message(err, "page")) from err

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
        props["Model"] = {
            "rich_text": [{"type": "text", "text": {"content": model[:200]}}]
        }
    if authors := metadata.get("authors"):
        joined = ", ".join(authors) if isinstance(authors, list) else str(authors)
        props["Authors"] = {
            "rich_text": [{"type": "text", "text": {"content": joined[:2000]}}]
        }
    if year := metadata.get("year"):
        with suppress(TypeError, ValueError):
            props["Year"] = {"number": int(year)}
    if export_type := metadata.get("type"):
        props["Type"] = {"select": {"name": export_type}}
    if tags := metadata.get("tags"):
        tag_list = tags if isinstance(tags, list) else [tags]
        props["Tags"] = {
            "multi_select": [{"name": t.strip()[:100]} for t in tag_list if t.strip()]
        }

    return props
