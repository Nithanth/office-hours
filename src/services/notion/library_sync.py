"""Library ↔ Notion sync: relational databases and comment Q&A.

The sync treats Notion as a live surface, not a dump target:

- Two linked databases (Papers ↔ Topics) auto-provisioned under the
  integration's anchor page, joined by a Notion relation property — so
  each topic page rolls up its papers and each paper rolls up to the
  research session that found it.
- Status is bidirectional: edits to a paper's Status select in Notion
  flow back into the app's ``saved_papers``.
- Comment Q&A: unanswered comment threads on a paper's Notion page are
  answered in-thread by the paper-scoped chat agent (polled via the API,
  no webhooks needed).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from services.notion.markdown_to_blocks import markdown_to_blocks
from services.notion.notion_exporter import NotionExporter, NotionExportError

_STATUS_LABELS = {"to_read": "To read", "reading": "Reading", "done": "Done"}
_LABEL_TO_STATUS = {v: k for k, v in _STATUS_LABELS.items()}


def _is_not_found(exc: Exception) -> bool:
    """True when the Notion object we're pointing at no longer exists —
    user deleted or archived the page/database in their workspace."""
    status = getattr(exc, "status", None)
    code = getattr(exc, "code", None)
    msg = str(exc).lower()
    return (
        status == 404
        or code == "object_not_found"
        or "could not find" in msg
        or "object_not_found" in msg
    )


_PAPERS_SCHEMA: dict[str, Any] = {
    "Name": {"title": {}},
    "Source URL": {"url": {}},
    "Authors": {"rich_text": {}},
    "Status": {
        "select": {
            "options": [
                {"name": "To read"},
                {"name": "Reading"},
                {"name": "Done"},
            ]
        }
    },
    "Saved": {"date": {}},
    # "Topic" relation is added post-creation via add_relation_property —
    # the relation needs the Topics DB id, which doesn't exist until both
    # databases are created.
}

_TOPICS_SCHEMA: dict[str, Any] = {
    "Name": {"title": {}},
    "Query": {"rich_text": {}},
    "Created": {"date": {}},
    # Stable record id ("report:…" / "topic:…") — the adoption key that lets
    # a reconnected sync reclaim its rows without relying on ambiguous titles.
    "Office Hours ID": {"rich_text": {}},
}


def _paper_properties(paper: dict, topic_page_id: str | None) -> dict:
    """Row properties for a saved-paper snapshot dict (see the route)."""
    authors = (
        paper["authors"].get("list") if isinstance(paper["authors"], dict) else None
    )
    joined = ", ".join(authors) if authors else ""
    props: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": paper["title"][:200]}}]
        },
        "Status": {"select": {"name": _STATUS_LABELS.get(paper["status"], "To read")}},
        "Saved": {"date": {"start": paper["saved_date"]}},
    }
    if paper["url"]:
        props["Source URL"] = {"url": paper["url"]}
    if joined:
        props["Authors"] = {
            "rich_text": [{"type": "text", "text": {"content": joined[:2000]}}]
        }
    if topic_page_id:
        props["Topic"] = {"relation": [{"id": topic_page_id}]}
    return props


def _topic_row_properties(topic: dict) -> dict:
    """Row properties for a workspace topic snapshot dict (see the route)."""
    props: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": topic["title"][:200]}}]
        },
        "Created": {"date": {"start": topic["created_date"]}},
    }
    seed = topic.get("seed_query") or ""
    if seed:
        props["Query"] = {
            "rich_text": [{"type": "text", "text": {"content": seed[:2000]}}]
        }
    props["Office Hours ID"] = {
        "rich_text": [
            {"type": "text", "text": {"content": f"topic:{topic['id']}"[:200]}}
        ]
    }
    return props


def _topic_body_markdown(topic: dict) -> str:
    """Page body for a mirrored topic — blog first (the shareable take),
    then notes (the running synthesis)."""
    base = _app_base_url()
    head = f"_Workspace mirror: {topic['kind']} topic._"
    if base:
        head += f" **Open in Office Hours:** {base}/topics/{topic['id']}"
    parts = [head, ""]
    parts.append("## Blog")
    parts.append(topic.get("blog_markdown") or "_No blog generated yet._")
    parts.append("")
    parts.append("## Notes")
    parts.append(topic.get("notes_markdown") or "_No notes generated yet._")
    return "\n".join(parts)


async def sync_topics(
    exporter: NotionExporter,
    *,
    topics_db_id: str,
    topics: list[dict],
) -> dict[str, Any]:
    """Mirror workspace topics into the Topics DB — one row per topic, with
    the living blog + notes as the page body (replaced only when its hash
    changed since the last sync). Each topic dict carries id, title, kind,
    seed_query, created_date, blog_markdown, notes_markdown, notion_page_id,
    and notion_body_hash.
    """
    import hashlib

    stats: dict[str, Any] = {
        "topic_page_ids": {},
        "body_hashes": {},
        "topics_created": 0,
        "topics_updated": 0,
        "bodies_written": 0,
        "bodies_skipped": 0,
        "failed": 0,
        "errors": [],
    }
    _, by_title, by_oh_id = await _existing_row_index(exporter, topics_db_id)
    claimed = {t["notion_page_id"] for t in topics if t.get("notion_page_id")}
    for topic in topics:
        tid = topic["id"]
        try:
            props = _topic_row_properties(topic)
            page_id, created = await _upsert_row(
                exporter,
                topics_db_id,
                props,
                page_id=topic.get("notion_page_id"),
                adopted_id=by_oh_id.get(f"topic:{tid}")
                or by_title.get((topic.get("title") or "")[:200]),
                claimed=claimed,
            )
            stats["topics_created" if created else "topics_updated"] += 1
            if page_id:
                stats["topic_page_ids"][tid] = page_id

            body = _topic_body_markdown(topic)
            body_hash = hashlib.sha256(body.encode()).hexdigest()
            if body_hash != topic.get("notion_body_hash"):
                if page_id and await exporter.page_is_empty(page_id):
                    await exporter.append_blocks(page_id, markdown_to_blocks(body))
                elif page_id:
                    await exporter.replace_page_content(page_id, body)
                stats["bodies_written"] += 1
                stats["body_hashes"][tid] = body_hash
            else:
                stats["bodies_skipped"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad topic can't stop the sync
            stats["failed"] += 1
            stats["errors"].append(f"topic {tid}: {exc}"[:200])
    return stats


def _topic_properties(report: dict, record_id: str) -> dict:
    """Row properties for a report snapshot dict (see the route)."""
    props: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": report["title"][:200]}}]
        },
        "Created": {"date": {"start": report["created_date"]}},
        "Office Hours ID": {
            "rich_text": [
                {"type": "text", "text": {"content": f"report:{record_id}"[:200]}}
            ]
        },
    }
    if report["original_query"]:
        props["Query"] = {
            "rich_text": [
                {"type": "text", "text": {"content": report["original_query"][:2000]}}
            ]
        }
    return props


async def provision_databases(
    exporter: NotionExporter,
    *,
    parent_page_id: str | None,
    parent_database_id: str | None,
    topics_database_id: str | None,
) -> tuple[str, str]:
    """Ensure the Papers and Topics databases exist; return their ids.

    Anchor rules: if a papers database is already configured it stays the
    anchor (Topics is created as a sibling under the same parent page). A
    configured page gets both databases created under it. Configured ids
    that point at objects the user deleted are dropped and re-provisioned.
    """
    anchor_page = parent_page_id
    papers_db = parent_database_id
    papers_schema: dict[str, Any] = {}
    if papers_db:
        try:
            db = await exporter.get_database(papers_db)
        except NotionExportError as exc:
            if not _is_not_found(exc):
                raise
            # The Papers DB was deleted in Notion — recreate it under the
            # anchor page instead of failing every sync forever.
            logger.info("notion_papers_db_missing_reprovision")
            papers_db = None
        else:
            papers_schema = db.get("properties") or {}
            parent = db.get("parent") or {}
            if parent.get("type") == "page_id":
                anchor_page = str(parent["page_id"])
            if "Source URL" not in papers_schema:
                # A foreign database (user's own, or a stale pick) can't hold
                # our columns — anchor fresh Office Hours DBs on its parent
                # page instead of writing rejected rows into it.
                logger.info("notion_foreign_db_destination_reanchor")
                papers_db = None

    if not papers_db:
        if not anchor_page:
            raise NotionExportError(
                "Pick a Notion page or database as the destination first. "
                "The library sync creates its databases there."
            )
        papers_db = await exporter.create_database(
            anchor_page, "Office Hours Papers", _PAPERS_SCHEMA
        )

    topics_db = topics_database_id
    topics_schema: dict[str, Any] = {}
    if topics_db:
        try:
            topics_schema = (await exporter.get_database(topics_db)).get(
                "properties"
            ) or {}
        except NotionExportError as exc:
            if not _is_not_found(exc):
                raise
            logger.info("notion_topics_db_missing_reprovision")
            topics_db = None
    if not topics_db:
        if not anchor_page:
            raise NotionExportError(
                "Pick a Notion page as the destination first. "
                "The library sync creates its databases there."
            )
        topics_db = await exporter.create_database(
            anchor_page, "Office Hours Topics", _TOPICS_SCHEMA
        )
    elif "Office Hours ID" not in topics_schema:
        # Backfill the adoption key on databases created before it existed.
        try:
            await exporter.add_property(topics_db, "Office Hours ID", {"rich_text": {}})
        except NotionExportError as exc:
            logger.warning("notion_ohid_backfill_failed", error=str(exc))

    # Wire the relation only when missing — re-adding an existing property
    # makes Notion reject the PATCH and spam the logs on every sync.
    assert papers_db is not None and topics_db is not None
    if "Topic" not in papers_schema:
        try:
            await exporter.add_relation_property(papers_db, "Topic", topics_db)
        except NotionExportError as exc:
            logger.warning("notion_relation_skip", error=str(exc))

    return papers_db, topics_db


async def _upsert_row(
    exporter: NotionExporter,
    database_id: str,
    props: dict[str, Any],
    *,
    page_id: str | None,
    adopted_id: str | None = None,
    claimed: set[str] | None = None,
) -> tuple[str | None, bool]:
    """Update *page_id*'s row, or create one — adopting a surviving row when
    the stored id is stale. Returns (page_id, created).

    Adopting matters after disconnect→reconnect: the disconnect clears our
    stored ids but the old rows still live in the user's workspace, so
    matching by Office Hours ID / Source URL / unique Name prevents a
    duplicate row per record. ``claimed`` reserves adopted rows so two
    same-titled records can never attach to the same page.
    """
    if page_id:
        try:
            await exporter.update_page_properties(page_id, props)
            return page_id, False
        except Exception as exc:
            if not _is_not_found(exc):
                raise
            logger.info("notion_row_missing_recreate", page_id=page_id)
    if adopted_id and (claimed is None or adopted_id not in claimed):
        try:
            await exporter.update_page_properties(adopted_id, props)
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        else:
            if claimed is not None:
                claimed.add(adopted_id)
            return adopted_id, False
    return await exporter.create_database_row(database_id, props), True


async def _existing_row_index(
    exporter: NotionExporter, database_id: str
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Match keys → row page id for every live row in the database.

    Returns ``(by_url, by_title, by_oh_id)`` built from the Source URL, Name
    and Office Hours ID columns — used to adopt rows left behind by a reset
    or a previous connection instead of duplicating them. Titles shared by
    several rows are dropped as ambiguous; Office Hours ID is the stable
    key. Best-effort: an empty index just means rows get created fresh.
    """
    by_url: dict[str, str] = {}
    by_oh_id: dict[str, str] = {}
    title_rows: dict[str, str] = {}
    dup_titles: set[str] = set()
    for row in await exporter.query_database_rows(database_id):
        rid = row.get("id")
        if not rid:
            continue
        props = row.get("properties") or {}
        url = (props.get("Source URL") or {}).get("url")
        if url:
            by_url[str(url)] = str(rid)
        oh_parts = (props.get("Office Hours ID") or {}).get("rich_text") or []
        oh_id = "".join(p.get("plain_text", "") for p in oh_parts).strip()
        if oh_id:
            by_oh_id[oh_id] = str(rid)
        title_parts = (props.get("Name") or {}).get("title") or []
        title = "".join(p.get("plain_text", "") for p in title_parts).strip()
        if title:
            if title in title_rows or title in dup_titles:
                dup_titles.add(title)
                title_rows.pop(title, None)
            else:
                title_rows[title] = str(rid)
    return by_url, title_rows, by_oh_id


def _app_base_url() -> str:
    """Public app origin for backlinks written into Notion pages."""
    import config as _cfg

    return (getattr(_cfg.config, "AUTH_REDIRECT_BASE_URL", "") or "").rstrip("/")


def _paper_intro_markdown(paper: dict, tldr: str | None) -> str:
    """Body markdown for a paper's Notion page — a TLDR when one can be
    generated, plus the pointer back to the source and a hint that the
    comments are live."""
    body = tldr or (
        "Saved to Office Hours. Comment a question on this page and the "
        "agent answers in-thread with paper-grounded citations."
    )
    lines = ["## About this paper", "", body, ""]
    if paper["url"]:
        lines.append(f"**Source:** {paper['url']}")
    base = _app_base_url()
    if base:
        lines.append(f"**Open in Office Hours:** {base}/library")
    return "\n".join(lines)


def _chat_blocks(
    messages: list[dict[str, Any]], *, heading: bool
) -> list[dict[str, Any]]:
    """Turn chat messages into Notion blocks mirroring the Q&A trail.

    ``heading`` adds a 'Q&A' section header — used the first time any
    chat history lands on the page. ``[n]`` chunk citations are stripped
    (they dangle without the app's Sources footer).
    """
    blocks: list[dict[str, Any]] = []
    if heading:
        blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": "Q&A"}}]
                },
            }
        )
    for msg in messages:
        content = re.sub(r"\s*\[\d+\]", "", msg.get("content") or "").strip()
        if not content:
            continue
        if msg.get("role") == "user":
            blocks.extend(markdown_to_blocks(f"**You:** {content}"))
        else:
            blocks.extend(markdown_to_blocks(f"**Office Hours:** {content}"))
    return blocks


async def sync_library(
    exporter: NotionExporter,
    *,
    papers_db_id: str,
    topics_db_id: str,
    papers: list[dict],
    reports: dict[str, dict],
    owner_id: str | None = None,
    topic_page_by_report_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Upsert topics + papers, then pull Status edits back.

    ``papers``/``reports`` are plain snapshot dicts (ORM objects detached
    from their session can't be read here). Returns a stats dict with a
    ``topic_page_ids`` map, ``paper_page_ids`` (paper_id → page id),
    ``status_updates`` (paper_id → status pulled from Notion), and
    ``synced_status`` (paper_id → the status now written in Notion) for the
    caller to persist.

    ``topic_page_by_report_id``: report_id → workspace-topic page id from
    ``sync_topics``; when a saved paper's report belongs to a topic, its
    Topic relation points at the living topic page instead of the
    per-report snapshot row.
    """
    stats: dict[str, Any] = {
        "topics_created": 0,
        "topics_updated": 0,
        "topic_page_ids": {},
        "paper_page_ids": {},
        "created": 0,
        "updated": 0,
        "failed": 0,
        "status_updates": {},
        "synced_status": {},
        "chat_synced": {},
        "errors": [],
    }

    _, topics_by_title, topics_by_ohid = await _existing_row_index(
        exporter, topics_db_id
    )
    topic_claimed = {
        r["notion_page_id"] for r in reports.values() if r.get("notion_page_id")
    }
    # Topics first — papers need their page ids for the relation.
    for report_id, report in reports.items():
        try:
            props = _topic_properties(report, report_id)
            page_id, created = await _upsert_row(
                exporter,
                topics_db_id,
                props,
                page_id=report["notion_page_id"],
                adopted_id=topics_by_ohid.get(f"report:{report_id}")
                or topics_by_title.get((report["title"] or "")[:200]),
                claimed=topic_claimed,
            )
            stats["topics_created" if created else "topics_updated"] += 1
            if page_id:
                stats["topic_page_ids"][report_id] = page_id
            # Fill the topic page body with the report — it becomes the
            # actual research explainer in Notion, not just a row. Only
            # writes when the page is empty so re-syncs never duplicate.
            if report.get("content"):
                base = _app_base_url()
                body_content = report["content"]
                if base:
                    body_content = (
                        f"_Synced from Office Hours. Open the live report:"
                        f" {base}/reports/{report_id}_\n\n" + body_content
                    )
                if page_id:
                    await exporter.ensure_page_content(page_id, body_content)
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(f"topic {report_id}: {exc}"[:200])

    papers_by_url, _, _ = await _existing_row_index(exporter, papers_db_id)
    paper_claimed = {p["notion_page_id"] for p in papers if p["notion_page_id"]}
    for paper in papers:
        try:
            # Three-way Status check BEFORE the upsert, against the last
            # synced value — edits on EITHER side propagate: Notion value
            # != last synced → pull into the app; app value != last synced
            # → the upsert below pushes it to Notion.
            last_synced = paper.get("notion_synced_status")
            if paper["notion_page_id"]:
                try:
                    label = await exporter.get_page_status(paper["notion_page_id"])
                except Exception:
                    label = None  # best-effort read; continue the sync
                notion_status = _LABEL_TO_STATUS.get(label or "")
                if notion_status and notion_status != last_synced:
                    stats["status_updates"][paper["paper_id"]] = notion_status
                    paper["status"] = notion_status

            # Relation prefers the living topic-workspace page over the
            # per-report snapshot row when the paper's report has a topic.
            topic_page = None
            if paper["report_id"]:
                if topic_page_by_report_id:
                    topic_page = topic_page_by_report_id.get(paper["report_id"])
                if not topic_page:
                    topic_page = stats["topic_page_ids"].get(paper["report_id"])
            props = _paper_properties(paper, topic_page)
            # URL is the only safe adoption key for papers — titles collide
            # too easily across different papers to trust.
            adopted = papers_by_url.get(paper["url"]) if paper.get("url") else None
            page_id, created = await _upsert_row(
                exporter,
                papers_db_id,
                props,
                page_id=paper["notion_page_id"],
                adopted_id=adopted,
                claimed=paper_claimed,
            )
            if page_id:
                stats["paper_page_ids"][paper["paper_id"]] = page_id
                stats["synced_status"][paper["paper_id"]] = paper["status"]
            stats["created" if created else "updated"] += 1

            page_id = stats["paper_page_ids"].get(paper["paper_id"])
            if page_id:
                # Page body: an intro + TLDR the first time we see the
                # page — what makes 'chat about this page' and Custom
                # Agents useful on papers, not just topics.
                try:
                    if await exporter.page_is_empty(page_id):
                        tldr = (
                            await _paper_tldr(paper["paper_id"], owner_id)
                            if owner_id
                            else None
                        )
                        await exporter.append_blocks(
                            page_id,
                            markdown_to_blocks(_paper_intro_markdown(paper, tldr)),
                        )
                except Exception as exc:
                    stats["errors"].append(
                        f"paper {paper['paper_id']} content: {exc}"[:200]
                    )
                # Q&A trail: append chat turns beyond the watermark so
                # the Notion page mirrors the learning record.
                msgs = paper.get("chat_messages") or []
                # A recreated page starts empty — the old watermark belongs
                # to the deleted row, so the trail re-mirrors from scratch.
                watermark = (
                    int(paper.get("notion_chat_synced") or 0) if not created else 0
                )
                new_msgs = msgs[watermark:]
                if new_msgs:
                    try:
                        await exporter.append_blocks(
                            page_id,
                            _chat_blocks(new_msgs, heading=watermark == 0),
                        )
                        stats["chat_synced"][paper["paper_id"]] = len(msgs)
                    except Exception as exc:
                        stats["errors"].append(
                            f"paper {paper['paper_id']} chat: {exc}"[:200]
                        )
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(f"paper {paper['paper_id']}: {exc}"[:200])

    return stats


# ---------------------------------------------------------------------------
# Comment Q&A
# ---------------------------------------------------------------------------


def unanswered_threads(
    comments: list[dict[str, Any]], bot_id: str | None
) -> list[tuple[str, str]]:
    """Return ``[(discussion_id, question_text)]`` for threads whose latest
    comment isn't from the integration bot.

    Pure function — unit-testable without the Notion API.
    """
    threads: dict[str, list[dict[str, Any]]] = {}
    for c in comments:
        did = c.get("discussion_id") or c.get("id")
        if did:
            threads.setdefault(did, []).append(c)

    out: list[tuple[str, str]] = []
    for did, thread in threads.items():
        thread.sort(key=lambda c: c.get("created_time", ""))
        last = thread[-1]
        author = (last.get("created_by") or {}).get("id")
        if bot_id and author == bot_id:
            continue  # we already answered (or are mid-answer)
        # Only treat comments ending in '?' as questions? No — Notion users
        # comment imperatively too ("explain this"). Every unanswered human
        # comment becomes a question to the agent.
        text_parts = [
            rt.get("plain_text", "")
            for c in thread
            for rt in (c.get("rich_text") or [])
            if (c.get("created_by") or {}).get("id") != bot_id
        ]
        question = " ".join(t for t in text_parts if t).strip()
        if question:
            out.append((did, question[:1900]))
    return out


async def answer_question(*, paper_id: str, question: str, user_id: str) -> str:
    """Answer a Notion comment with the paper-scoped chat agent."""
    import config as _cfg
    from api.deps import get_llm_service
    from services.chat.chat_runtime import build_prompt
    from services.chat.chat_state import ChatMessage, get_chat_state
    from services.core import model_prefs

    service = get_llm_service()
    if service is None:
        raise NotionExportError(
            "The assistant isn't running in this process. Answer comments from the running app."
        )

    # Ingest the paper on demand — a saved paper may never have been opened,
    # and without an index the answer degenerates to "no context".
    try:
        from services.paper.text_extractor import TextExtractor

        await TextExtractor.instance().ensure_cached(
            paper_id, paper_id=paper_id, prefer_exa=True
        )
    except Exception:
        pass  # best-effort; build_prompt still works off whatever exists

    state = get_chat_state(user_id)
    state.append(paper_id, ChatMessage(role="user", content=question))
    bundle = await build_prompt(
        state, paper_id, audience_type="semi_technical", user_id=user_id
    )
    selected_key = _cfg.config.DEFAULT_MODEL_KEY
    model_cfg = _cfg.config.AVAILABLE_MODELS[selected_key]
    model_id = model_prefs.resolve_model_id(selected_key)
    answer = await service.generate_completion(
        provider_name=model_cfg["provider"],
        model_name=model_id,
        prompt=bundle.prompt,
        max_tokens=800,
        temperature=_cfg.config.CHAT_TEMPERATURE,
    )
    state.append(paper_id, ChatMessage(role="assistant", content=answer))
    return str(answer)


async def _paper_tldr(paper_id: str, owner_id: str | None) -> str | None:
    """One-line TLDR for a paper page's body, from the same grounded
    agent that answers comments. Best-effort — any failure just means
    the page gets the fallback intro instead."""
    if not owner_id:
        return None
    try:
        tldr = await answer_question(
            paper_id=paper_id,
            question=(
                "Give a concise 3-4 sentence TLDR of this paper for "
                "someone deciding whether to read it."
            ),
            user_id=owner_id,
        )
        return re.sub(r"\s*\[\d+\]", "", tldr).strip() or None
    except Exception:
        return None


async def answer_comments_for_paper(
    exporter: NotionExporter,
    *,
    notion_page_id: str,
    paper_id: str,
    user_id: str,
    bot_id: str | None,
) -> int:
    """Reply to every unanswered comment thread on a paper's Notion page.

    Returns the number of threads answered.
    """
    comments = await exporter.list_comments(notion_page_id)
    answered = 0
    for discussion_id, question in unanswered_threads(comments, bot_id):
        try:
            answer = await answer_question(
                paper_id=paper_id, question=question, user_id=user_id
            )
            # [n] chunk citations dangle in Notion — there's no Sources
            # footer to resolve them, so drop the markers entirely.
            answer = re.sub(r"\s*\[\d+\]", "", answer)
            # Attribute the reply — a comment thread with several humans
            # shouldn't read like one of them answered.
            await exporter.reply_to_discussion(
                discussion_id, f"**Office Hours:** {answer}"
            )
            answered += 1
        except Exception as exc:
            logger.warning(
                "notion_comment_answer_failed",
                discussion_id=discussion_id,
                error=str(exc),
            )
    return answered
