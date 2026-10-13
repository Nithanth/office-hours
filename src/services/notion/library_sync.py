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

from typing import Any

from loguru import logger

from services.notion.notion_exporter import NotionExporter, NotionExportError

_STATUS_LABELS = {"to_read": "To read", "reading": "Reading", "done": "Done"}
_LABEL_TO_STATUS = {v: k for k, v in _STATUS_LABELS.items()}

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


def _topic_properties(report: dict) -> dict:
    """Row properties for a report snapshot dict (see the route)."""
    props: dict = {
        "Name": {
            "title": [{"type": "text", "text": {"content": report["title"][:200]}}]
        },
        "Created": {"date": {"start": report["created_date"]}},
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
    configured page gets both databases created under it.
    """
    papers_db = parent_database_id
    if papers_db:
        anchor_page = await exporter.get_database_parent_page(papers_db)
    else:
        anchor_page = parent_page_id
    if not anchor_page:
        raise NotionExportError(
            "Pick a Notion page or database as the destination first — "
            "the library sync creates its databases there."
        )

    if not papers_db:
        papers_db = await exporter.create_database(
            anchor_page, "Office Hours Papers", _PAPERS_SCHEMA
        )

    topics_db = topics_database_id
    if not topics_db:
        topics_db = await exporter.create_database(
            anchor_page, "Office Hours Topics", _TOPICS_SCHEMA
        )
    # Wire the relation once both exist — if the property already exists
    # Notion rejects the update, which we tolerate as already-provisioned.
    assert papers_db is not None and topics_db is not None
    try:
        await exporter.add_relation_property(papers_db, "Topic", topics_db)
    except NotionExportError as exc:
        logger.warning("notion_relation_skip", error=str(exc))

    return papers_db, topics_db


async def sync_library(
    exporter: NotionExporter,
    *,
    papers_db_id: str,
    topics_db_id: str,
    papers: list[dict],
    reports: dict[str, dict],
) -> dict[str, Any]:
    """Upsert topics + papers, then pull Status edits back.

    ``papers``/``reports`` are plain snapshot dicts (ORM objects detached
    from their session can't be read here). Returns a stats dict with a
    ``topic_page_ids`` map, ``paper_page_ids`` (paper_id → new page id),
    and ``status_updates`` (paper_id → new status) for the caller to persist.
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
        "errors": [],
    }

    # Topics first — papers need their page ids for the relation.
    for report_id, report in reports.items():
        try:
            props = _topic_properties(report)
            if report["notion_page_id"]:
                await exporter.update_page_properties(report["notion_page_id"], props)
                stats["topics_updated"] += 1
                stats["topic_page_ids"][report_id] = report["notion_page_id"]
            else:
                page_id = await exporter.create_database_row(topics_db_id, props)
                stats["topics_created"] += 1
                stats["topic_page_ids"][report_id] = page_id
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(f"topic {report_id}: {exc}"[:200])

    for paper in papers:
        try:
            topic_page = (
                stats["topic_page_ids"].get(paper["report_id"])
                if paper["report_id"]
                else None
            )
            props = _paper_properties(paper, topic_page)
            if paper["notion_page_id"]:
                await exporter.update_page_properties(paper["notion_page_id"], props)
                stats["updated"] += 1
                stats["paper_page_ids"][paper["paper_id"]] = paper["notion_page_id"]
            else:
                page_id = await exporter.create_database_row(papers_db_id, props)
                if page_id:
                    stats["paper_page_ids"][paper["paper_id"]] = page_id
                stats["created"] += 1
        except Exception as exc:
            stats["failed"] += 1
            stats["errors"].append(f"paper {paper['paper_id']}: {exc}"[:200])

    # Pull-back: Status edits made in Notion win over the app's value.
    for paper in papers:
        page_id = (
            stats["paper_page_ids"].get(paper["paper_id"]) or paper["notion_page_id"]
        )
        if not page_id:
            continue
        try:
            label = await exporter.get_page_status(page_id)
        except Exception:
            continue
        status = _LABEL_TO_STATUS.get(label or "")
        if status and status != paper["status"]:
            stats["status_updates"][paper["paper_id"]] = status

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
    from services.chat.chat_runtime import build_prompt
    from services.chat.chat_state import ChatMessage, get_chat_state
    from services.core import model_prefs
    from services.core.llm_service import llm_service

    state = get_chat_state(user_id)
    state.append(paper_id, ChatMessage(role="user", content=question))
    bundle = await build_prompt(
        state, paper_id, audience_type="semi_technical", user_id=user_id
    )
    selected_key = _cfg.config.DEFAULT_MODEL_KEY
    model_cfg = _cfg.config.AVAILABLE_MODELS[selected_key]
    model_id = model_prefs.resolve_model_id(selected_key)
    answer = await llm_service.generate_completion(
        provider_name=model_cfg["provider"],
        model_name=model_id,
        prompt=bundle.prompt,
        max_tokens=800,
        temperature=_cfg.config.CHAT_TEMPERATURE,
    )
    state.append(paper_id, ChatMessage(role="assistant", content=answer))
    return str(answer)


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
            await exporter.reply_to_discussion(discussion_id, answer)
            answered += 1
        except Exception as exc:
            logger.warning(
                "notion_comment_answer_failed",
                discussion_id=discussion_id,
                error=str(exc),
            )
    return answered
