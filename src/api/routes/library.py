"""Persistent library: save papers, track reading status, sync to Notion."""

from __future__ import annotations

import asyncio
import html
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from services.auth.session import get_auth_user_id
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import SavedPaper
from services.persistence.repositories import (
    get_notion_integration,
    get_report,
    get_saved_paper,
    get_topic,
    list_paper_chat_messages,
    list_saved_paper_ids,
    list_saved_papers,
    list_topics,
    notion_access_token,
    save_paper,
    set_notion_databases,
    set_report_notion_page,
    set_saved_paper_notion_chat_count,
    set_saved_paper_notion_page,
    set_saved_paper_notion_status,
    set_saved_paper_status,
    set_topic_notion,
    unsave_paper,
)
from utils.static_assets import static_url

router = APIRouter(tags=["library"])

_STATUS_LABELS = {
    "to_read": "To read",
    "reading": "Reading",
    "done": "Done",
}


def _owner_id(request: Request) -> str | None:
    owner = prefs_owner_id(request)
    return str(owner) if owner else None


def _no_owner() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "detail": "Could not determine your session."},
        status_code=400,
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@router.post("/api/library/save")
async def library_save(request: Request) -> JSONResponse:
    owner_id = _owner_id(request)
    if not owner_id:
        return _no_owner()
    if not database_available():
        return JSONResponse({"ok": False, "detail": "Library not configured"})

    form = await request.form()
    paper_id = str(form.get("paper_id") or "").strip()
    title = str(form.get("title") or "").strip() or paper_id
    url = str(form.get("url") or "").strip() or None
    authors_raw = str(form.get("authors") or "").strip()
    report_id = str(form.get("report_id") or "").strip() or None
    if not paper_id:
        return JSONResponse(
            {"ok": False, "detail": "paper_id required"}, status_code=400
        )

    authors: dict = {}
    if authors_raw:
        author_list = [a.strip() for a in authors_raw.split(";") if a.strip()]
        authors = {"list": author_list}

    async for session in session_scope():
        await save_paper(
            session,
            owner_id=owner_id,
            paper_id=paper_id,
            title=title,
            url=url,
            authors=authors,
            report_id=report_id,
        )
    _schedule_notion_sync(owner_id, paper_id)
    return JSONResponse({"ok": True, "saved": True, "paper_id": paper_id})


@router.post("/api/library/unsave")
async def library_unsave(request: Request) -> JSONResponse:
    owner_id = _owner_id(request)
    if not owner_id:
        return _no_owner()
    if not database_available():
        return JSONResponse({"ok": False, "detail": "Library not configured"})

    form = await request.form()
    paper_id = str(form.get("paper_id") or "").strip()
    notion_page_id: str | None = None
    async for session in session_scope():
        saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
        notion_page_id = saved.notion_page_id if saved else None
        removed = await unsave_paper(session, owner_id=owner_id, paper_id=paper_id)
    if notion_page_id:
        _schedule_notion_archive(owner_id, notion_page_id)
    return JSONResponse({"ok": True, "saved": False, "removed": removed})


@router.post("/api/library/status")
async def library_status(request: Request) -> JSONResponse:
    owner_id = _owner_id(request)
    if not owner_id:
        return _no_owner()
    if not database_available():
        return JSONResponse({"ok": False, "detail": "Library not configured"})

    form = await request.form()
    paper_id = str(form.get("paper_id") or "").strip()
    status = str(form.get("status") or "").strip()
    async for session in session_scope():
        saved = await set_saved_paper_status(
            session, owner_id=owner_id, paper_id=paper_id, status=status
        )
    if saved is None:
        return JSONResponse(
            {"ok": False, "detail": "Unknown paper or status"}, status_code=404
        )
    _schedule_notion_sync(owner_id, paper_id)
    return JSONResponse({"ok": True, "status": saved.status})


@router.get("/api/library/ids")
async def library_ids(request: Request) -> JSONResponse:
    owner_id = _owner_id(request)
    if not owner_id or not database_available():
        return JSONResponse({"ok": True, "ids": []})
    async for session in session_scope():
        ids = await list_saved_paper_ids(session, owner_id=owner_id)
    return JSONResponse({"ok": True, "ids": sorted(ids)})


# ---------------------------------------------------------------------------
# Notion sync
# ---------------------------------------------------------------------------


async def _load_integration(
    user_id: str,
) -> tuple[dict[str, str | None] | None, str | None]:
    """Snapshot of the Notion integration fields + decrypted token.

    Reads every scalar inside the session — the ORM object detaches when
    the session closes, so callers get plain data instead.
    """
    async for session in session_scope():
        integration = await get_notion_integration(session, user_id=user_id)
        if integration is None:
            return None, None
        snapshot = {
            "parent_page_id": integration.parent_page_id,
            "parent_database_id": integration.parent_database_id,
            "topics_database_id": integration.topics_database_id,
            "bot_id": integration.bot_id,
        }
        return snapshot, notion_access_token(integration)
    return None, None


def _schedule_notion_sync(owner_id: str, paper_id: str) -> None:
    """Fire-and-forget single-paper sync so saves and status changes land
    in Notion without a manual button press. No-ops for users without a
    connected integration."""
    task = asyncio.create_task(_sync_paper_to_notion(owner_id, paper_id))
    task.add_done_callback(
        lambda t: (
            logger.warning("notion_auto_sync_failed", error=str(t.exception()))
            if t.exception()
            else None
        )
    )


def _schedule_notion_archive(owner_id: str, notion_page_id: str) -> None:
    """Archive a paper's Notion row in the background when it leaves the
    library."""
    task = asyncio.create_task(_archive_notion_page(owner_id, notion_page_id))
    task.add_done_callback(
        lambda t: (
            logger.warning("notion_archive_failed", error=str(t.exception()))
            if t.exception()
            else None
        )
    )


async def _archive_notion_page(owner_id: str, notion_page_id: str) -> None:
    from services.notion.notion_exporter import NotionExporter

    integration, token = await _load_integration(owner_id)
    if integration is None or not token:
        return
    exporter = NotionExporter(token)
    try:
        await exporter.archive_page(notion_page_id)
    finally:
        await exporter.close()


async def _sync_paper_to_notion(owner_id: str, paper_id: str) -> None:
    """Single-paper version of the notion-sync endpoint: upsert the row,
    fill the page body, mirror new chat turns, answer open comments."""
    from services.notion.library_sync import (
        answer_comments_for_paper,
        provision_databases,
        sync_library,
    )
    from services.notion.notion_exporter import NotionExporter

    integration, token = await _load_integration(owner_id)
    if integration is None or not token:
        return

    async for session in session_scope():
        saved = await get_saved_paper(session, owner_id=owner_id, paper_id=paper_id)
        if saved is None:
            return
        chat = await list_paper_chat_messages(
            session, owner_id=owner_id, paper_id=paper_id
        )
        row = {
            "paper_id": saved.paper_id,
            "title": saved.title,
            "url": saved.url,
            "authors": saved.authors,
            "status": saved.status,
            "report_id": saved.report_id,
            "notion_page_id": saved.notion_page_id,
            "notion_synced_status": saved.notion_synced_status,
            "notion_chat_synced": saved.notion_chat_synced,
            "saved_date": saved.created_at.date().isoformat(),
            "chat_messages": [{"role": m.role, "content": m.content} for m in chat],
        }
    reports: dict = {}
    if row["report_id"]:
        async for session in session_scope():
            report = await get_report(
                session, user_id=owner_id, report_id=row["report_id"]
            )
            if report:
                reports[row["report_id"]] = {
                    "title": report.title,
                    "original_query": report.original_query,
                    "created_date": report.created_at.date().isoformat(),
                    "notion_page_id": report.notion_page_id,
                    "content": report.report_text,
                    "topic_id": report.topic_id,
                }
    # Point the paper's Topic relation at the living topic page when the
    # report belongs to a topic that already has one — otherwise this
    # single-paper sync would overwrite it back to the report row.
    topic_page_by_report: dict[str, str] = {}
    rid = row["report_id"]
    if rid and reports.get(rid, {}).get("topic_id"):
        async for session in session_scope():
            topic = await get_topic(
                session, owner_id=owner_id, topic_id=reports[rid]["topic_id"]
            )
            if topic is not None and topic.notion_page_id:
                topic_page_by_report[rid] = topic.notion_page_id

    exporter = NotionExporter(token)
    try:
        papers_db_id, topics_db_id = await provision_databases(
            exporter,
            parent_page_id=integration["parent_page_id"],
            parent_database_id=integration["parent_database_id"],
            topics_database_id=integration["topics_database_id"],
        )
        async for session in session_scope():
            await set_notion_databases(
                session,
                user_id=owner_id,
                parent_database_id=papers_db_id,
                topics_database_id=topics_db_id,
            )
        stats = await sync_library(
            exporter,
            papers_db_id=papers_db_id,
            topics_db_id=topics_db_id,
            papers=[row],
            reports=reports,
            owner_id=owner_id,
            topic_page_by_report_id=topic_page_by_report,
        )
        # Answer any open comment threads while we're here — questions
        # get replies whenever library state changes, no button needed.
        page_id = stats["paper_page_ids"].get(paper_id)
        if page_id:
            await answer_comments_for_paper(
                exporter,
                notion_page_id=page_id,
                paper_id=paper_id,
                user_id=owner_id,
                bot_id=integration["bot_id"],
            )
    finally:
        await exporter.close()

    for report_id, new_page in stats["topic_page_ids"].items():
        if not reports.get(report_id, {}).get("notion_page_id"):
            async for session in session_scope():
                await set_report_notion_page(
                    session,
                    user_id=owner_id,
                    report_id=report_id,
                    notion_page_id=new_page,
                )
    synced = stats["synced_status"].get(paper_id)
    if (page_id and page_id != row["notion_page_id"]) or synced:
        async for session in session_scope():
            await set_saved_paper_notion_page(
                session,
                owner_id=owner_id,
                paper_id=paper_id,
                notion_page_id=page_id or row["notion_page_id"],
                synced_status=synced,
            )
    pulled = stats["status_updates"].get(paper_id)
    if pulled:
        async for session in session_scope():
            await set_saved_paper_notion_status(
                session, owner_id=owner_id, paper_id=paper_id, status=pulled
            )
    chat_count = stats["chat_synced"].get(paper_id)
    if chat_count:
        async for session in session_scope():
            await set_saved_paper_notion_chat_count(
                session, owner_id=owner_id, paper_id=paper_id, count=chat_count
            )


@router.post("/api/library/notion-sync")
async def library_notion_sync(request: Request) -> JSONResponse:
    user_id = get_auth_user_id(request)
    if not user_id:
        return JSONResponse(
            {"ok": False, "detail": "Sign in to sync your library to Notion."},
            status_code=401,
        )
    if not database_available():
        return JSONResponse({"ok": False, "detail": "Library not configured"})

    integration, token = await _load_integration(str(user_id))
    if integration is None or not token:
        return JSONResponse(
            {
                "ok": False,
                "detail": "Connect Notion first (Settings → Notion, or a report's Export to Notion).",
            },
            status_code=400,
        )

    from services.notion.library_sync import (
        provision_databases,
        sync_library,
        sync_topics,
    )
    from services.notion.notion_exporter import NotionExporter

    exporter = NotionExporter(token)
    try:
        # Provision: ensure Papers + Topics databases (auto-created under the
        # anchor page) and the relation between them.
        papers_db_id, topics_db_id = await provision_databases(
            exporter,
            parent_page_id=integration["parent_page_id"],
            parent_database_id=integration["parent_database_id"],
            topics_database_id=integration["topics_database_id"],
        )
        async for session in session_scope():
            await set_notion_databases(
                session,
                user_id=str(user_id),
                parent_database_id=papers_db_id,
                topics_database_id=topics_db_id,
            )

        # Snapshot papers + their reports as plain dicts inside sessions
        # (ORM objects detach and can't be read after the session closes).
        async for session in session_scope():
            papers = await list_saved_papers(session, owner_id=str(user_id))
            paper_rows = []
            for p in papers:
                chat = await list_paper_chat_messages(
                    session, owner_id=str(user_id), paper_id=p.paper_id
                )
                paper_rows.append(
                    {
                        "paper_id": p.paper_id,
                        "title": p.title,
                        "url": p.url,
                        "authors": p.authors,
                        "status": p.status,
                        "report_id": p.report_id,
                        "notion_page_id": p.notion_page_id,
                        "notion_synced_status": p.notion_synced_status,
                        "notion_chat_synced": p.notion_chat_synced,
                        "saved_date": p.created_at.date().isoformat(),
                        "chat_messages": [
                            {"role": m.role, "content": m.content} for m in chat
                        ],
                    }
                )
        reports: dict = {}
        for row in paper_rows:
            report_id = row["report_id"]
            if report_id and report_id not in reports:
                async for session in session_scope():
                    report = await get_report(
                        session, user_id=str(user_id), report_id=report_id
                    )
                    if report:
                        reports[report_id] = {
                            "title": report.title,
                            "original_query": report.original_query,
                            "created_date": report.created_at.date().isoformat(),
                            "notion_page_id": report.notion_page_id,
                            "content": report.report_text,
                            "topic_id": report.topic_id,
                        }

        # Workspace topics — mirrored into the same Topics DB, one row per
        # topic with the living blog + notes as the page body.
        async for session in session_scope():
            topics = await list_topics(session, owner_id=str(user_id), limit=200)
            topic_rows = [
                {
                    "id": t.id,
                    "title": t.title,
                    "kind": t.kind,
                    "seed_query": t.seed_query,
                    "created_date": t.created_at.date().isoformat(),
                    "blog_markdown": t.blog_markdown,
                    "notes_markdown": t.notes_markdown,
                    "notion_page_id": t.notion_page_id,
                    "notion_body_hash": t.notion_body_hash,
                }
                for t in topics
            ]

        ws_stats = await sync_topics(
            exporter, topics_db_id=topics_db_id, topics=topic_rows
        )
        # Saved papers relate to the living topic page when their report
        # belongs to one; otherwise to the per-report snapshot row.
        topic_page_by_report = {
            rid: ws_stats["topic_page_ids"][reports[rid]["topic_id"]]
            for rid in reports
            if reports[rid].get("topic_id")
            and reports[rid]["topic_id"] in ws_stats["topic_page_ids"]
        }

        stats = await sync_library(
            exporter,
            papers_db_id=papers_db_id,
            topics_db_id=topics_db_id,
            papers=paper_rows,
            reports=reports,
            owner_id=str(user_id),
            topic_page_by_report_id=topic_page_by_report,
        )
    finally:
        await exporter.close()

    # Persist notion page ids + pulled-back status changes.
    for report_id, page_id in stats["topic_page_ids"].items():
        if not reports.get(report_id, {}).get("notion_page_id"):
            async for session in session_scope():
                await set_report_notion_page(
                    session,
                    user_id=str(user_id),
                    report_id=report_id,
                    notion_page_id=page_id,
                )
    for row in paper_rows:
        page_id = stats["paper_page_ids"].get(row["paper_id"])
        synced = stats["synced_status"].get(row["paper_id"])
        if (page_id and page_id != row["notion_page_id"]) or synced:
            async for session in session_scope():
                await set_saved_paper_notion_page(
                    session,
                    owner_id=str(user_id),
                    paper_id=row["paper_id"],
                    notion_page_id=page_id or row["notion_page_id"],
                    synced_status=synced,
                )
    for paper_id, status in stats["status_updates"].items():
        async for session in session_scope():
            await set_saved_paper_notion_status(
                session, owner_id=str(user_id), paper_id=paper_id, status=status
            )
    for paper_id, count in stats["chat_synced"].items():
        async for session in session_scope():
            await set_saved_paper_notion_chat_count(
                session, owner_id=str(user_id), paper_id=paper_id, count=count
            )

    # Persist workspace-topic sync bookkeeping (page ids + body hashes).
    for row in topic_rows:
        tid = row["id"]
        page_id = ws_stats["topic_page_ids"].get(tid)
        body_hash = ws_stats["body_hashes"].get(tid)
        if (page_id and page_id != row["notion_page_id"]) or body_hash:
            async for session in session_scope():
                await set_topic_notion(
                    session,
                    owner_id=str(user_id),
                    topic_id=tid,
                    notion_page_id=page_id,
                    notion_body_hash=body_hash,
                )

    return JSONResponse(
        {
            "ok": stats["failed"] == 0 and ws_stats["failed"] == 0,
            "created": stats["created"],
            "updated": stats["updated"],
            "topics_created": stats["topics_created"] + ws_stats["topics_created"],
            "topics_updated": stats["topics_updated"] + ws_stats["topics_updated"],
            "topic_bodies_written": ws_stats["bodies_written"],
            "status_pulled": len(stats["status_updates"]),
            "failed": stats["failed"] + ws_stats["failed"],
            "errors": (stats["errors"] + ws_stats["errors"])[:5],
        }
    )


@router.post("/api/library/notion-answer-comments")
async def library_notion_answer_comments(request: Request) -> JSONResponse:
    """Poll synced paper pages for unanswered comments and reply in-thread."""
    user_id = get_auth_user_id(request)
    if not user_id:
        return JSONResponse(
            {"ok": False, "detail": "Sign in to answer Notion comments."},
            status_code=401,
        )
    if not database_available():
        return JSONResponse({"ok": False, "detail": "Library not configured"})

    integration, token = await _load_integration(str(user_id))
    if integration is None or not token:
        return JSONResponse(
            {"ok": False, "detail": "Connect Notion first."}, status_code=400
        )

    from services.notion.library_sync import answer_comments_for_paper
    from services.notion.notion_exporter import NotionExporter, NotionExportError

    async for session in session_scope():
        papers = await list_saved_papers(session, owner_id=str(user_id))
        synced = [
            (p.paper_id, str(p.notion_page_id)) for p in papers if p.notion_page_id
        ]

    exporter = NotionExporter(token)
    answered = failed = 0
    first_error: str | None = None
    try:
        for paper_id, notion_page_id in synced:
            try:
                answered += await answer_comments_for_paper(
                    exporter,
                    notion_page_id=str(notion_page_id),
                    paper_id=paper_id,
                    user_id=str(user_id),
                    bot_id=integration["bot_id"],
                )
            except NotionExportError as exc:
                failed += 1
                first_error = first_error or str(exc)
    finally:
        await exporter.close()

    return JSONResponse(
        {
            "ok": True,
            "answered": answered,
            "failed_pages": failed,
            "detail": first_error
            or ("No unanswered comments." if answered == 0 else None),
        }
    )


# ---------------------------------------------------------------------------
# Library page
# ---------------------------------------------------------------------------


def _library_shell(content_html: str) -> str:
    from pages.shell import page_shell

    return str(
        page_shell(
            "Library",
            content_html,
            active="library",
            scripts=f"""<script src="{static_url('/static/js/library.js')}" defer></script>
  <script type="module">
    import "{static_url('/static/js/chat/stream.js')}";
    import "{static_url('/static/js/chat/ui.js')}";
    import "{static_url('/static/js/chat/events.js')}";
    import "{static_url('/static/js/chat/snippet.js')}";
  </script>""",
            wrap_class="lib-wrap",
        )
    )


def _status_options(current: str, paper_id: str) -> str:
    opts = []
    for value, label in _STATUS_LABELS.items():
        sel = " selected" if value == current else ""
        opts.append(f'<option value="{value}"{sel}>{label}</option>')
    return (
        f'<select class="lib-status-select" data-paper-id="{html.escape(paper_id, quote=True)}">'
        + "".join(opts)
        + "</select>"
    )


def _paper_item(paper: SavedPaper) -> str:
    title = html.escape(paper.title or "Untitled")
    pid_attr = html.escape(paper.paper_id, quote=True)
    pid_q = quote(paper.paper_id, safe="")
    viewer_url = f"/viewer/{pid_q}?title={quote(paper.title or 'Paper', safe='')}"
    if paper.url:
        viewer_url += f"&url={quote(paper.url, safe='')}"
    authors = paper.authors.get("list") if isinstance(paper.authors, dict) else None
    authors_str = html.escape(", ".join(authors[:6])) if authors else ""
    status_label = _STATUS_LABELS.get(paper.status, "To read")
    open_link = (
        f'<a href="{html.escape(paper.url, quote=True)}" target="_blank">source ↗</a>'
        if paper.url
        else ""
    )
    report_link = (
        f' · <a href="/reports/{html.escape(paper.report_id)}">topic</a>'
        if paper.report_id
        else ""
    )
    return f"""<article class="lib-item" data-paper-id="{pid_attr}">
  <h2><a href="{html.escape(paper.url or "#", quote=True)}" target="_blank">{title}</a>
    <span class="lib-status-chip">{status_label}</span></h2>
  <div class="lib-meta">{authors_str}</div>
  <div class="lib-actions">
    <button hx-get="{viewer_url}" hx-target="#modal-container" hx-swap="innerHTML">Read &amp; chat</button>
    {open_link}{report_link}
    {_status_options(paper.status, paper.paper_id)}
    <button class="lib-remove-btn" data-paper-id="{pid_attr}">Remove</button>
  </div>
</article>"""


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, status: str | None = None) -> HTMLResponse:
    owner_id = _owner_id(request)
    if not owner_id:
        raise HTTPException(status_code=400, detail="No session")
    if not database_available():
        raise HTTPException(status_code=503, detail="Library is not configured.")

    if status and status not in SavedPaper.READING_STATUSES:
        status = None

    signed_in = bool(get_auth_user_id(request))
    notion_connected = False
    notion_needs_destination = False
    async for session in session_scope():
        papers = await list_saved_papers(session, owner_id=owner_id, status=status)
        if signed_in:
            integration = await get_notion_integration(
                session, user_id=str(get_auth_user_id(request))
            )
            if integration:
                notion_connected = bool(
                    integration.parent_page_id or integration.parent_database_id
                )
                notion_needs_destination = not notion_connected

    tabs = [
        '<a href="/library"' + (' class="active"' if not status else "") + ">All</a>"
    ]
    for value, label in _STATUS_LABELS.items():
        active = ' class="active"' if status == value else ""
        tabs.append(f'<a href="/library?status={value}"{active}>{label}</a>')

    if notion_connected:
        sync_btn = (
            '<button id="lib-notion-sync-btn" class="oh-tip" type="button" '
            'data-tip="Re-push every saved paper to your Notion databases — sync already runs automatically when you save papers or change a status; use this only if Notion drifted">'
            "Re-sync library</button>"
            '<button id="lib-notion-answer-btn" class="oh-tip" type="button" '
            'data-tip="Scan your synced Notion paper pages for unanswered comments and reply to them with the agent">'
            "Answer Notion comments</button>"
        )
        notion_card = ""
    elif notion_needs_destination:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Notion connected</strong> — one last step: pick where your library lives.
  <div class="lib-notion-pick">
    <select id="lib-notion-destination"><option value="">Loading your pages…</option></select>
    <button id="lib-notion-set-dest" type="button">Set up sync</button>
  </div>
  <div class="lib-notion-hint">Office Hours creates two linked databases — Papers ↔ Topics — under the page you pick.</div>
</div>"""
    elif signed_in:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Mirror your library in Notion</strong> — papers, topics and reading status sync automatically; questions in page comments get answered by the agent.
  <div class="lib-notion-pick"><a class="lib-notion-connect" href="/integrations/notion/connect?next=/library">Connect Notion</a></div>
</div>"""
    else:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Mirror your library in Notion</strong> — sign in to connect your workspace.
</div>"""

    if papers:
        items = "".join(_paper_item(p) for p in papers)
    elif status:
        items = (
            '<div class="empty-state-card"><strong>Nothing here yet.</strong>'
            "<p>No papers with this status — pick another tab above, or add "
            "papers from a search.</p></div>"
        )
    else:
        items = (
            '<div class="empty-state-card"><strong>Your library is empty.</strong>'
            "<p>Save papers from search results with the bookmark icon and "
            "they'll land here — anonymous sessions keep a library too.</p>"
            '<a href="/">Search papers</a></div>'
        )

    content = f"""<div class="lib-header">
      <h1>Library</h1>
      {sync_btn}
      <a href="/">+ New Search</a>
    </div>
    {notion_card}
    <div class="lib-tabs">{"".join(tabs)}</div>
    <div id="lib-sync-status" style="font-size:0.8rem;color:var(--fg-muted,#888);margin-bottom:0.75rem;"></div>
    {items}
    <script>
      (function() {{
        async function post(url, data) {{
          const r = await fetch(url, {{
            method: "POST",
            headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
            body: new URLSearchParams(data).toString(),
          }});
          return r.json();
        }}
        // Notion destination picker — only present when connected with no anchor.
        const destSel = document.getElementById("lib-notion-destination");
        if (destSel) {{
          fetch("/api/notion/destinations").then(r => r.json()).then(res => {{
            const dests = (res.destinations || []).filter(d => d.type === "page" || d.type === "database");
            destSel.innerHTML = dests.length
              ? dests.map(d => `<option value="${{d.id}}" data-type="${{d.type}}">${{d.type === "page" ? "📄" : "🗃"}} ${{d.title}}</option>`).join("")
              : '<option value="">No shared pages — re-connect and share a page</option>';
          }});
        }}
        const setDest = document.getElementById("lib-notion-set-dest");
        if (setDest) {{
          setDest.addEventListener("click", function() {{
            const opt = destSel.options[destSel.selectedIndex];
            if (!opt || !opt.value) return;
            setDest.disabled = true;
            post("/api/notion/destination", {{
              destination_id: opt.value,
              destination_type: opt.dataset.type,
            }}).then(res => {{
              if (res.ok) {{
                const statusEl = document.getElementById("lib-sync-status");
                statusEl.textContent = "All set — papers now sync to Notion automatically.";
                document.querySelector(".lib-notion-card").remove();
              }} else {{
                setDest.disabled = false;
              }}
            }});
          }});
        }}
        document.addEventListener("change", function(ev) {{
          const sel = ev.target.closest(".lib-status-select");
          if (!sel) return;
          post("/api/library/status", {{paper_id: sel.dataset.paperId, status: sel.value}})
            .then(() => location.reload());
        }});
        document.addEventListener("click", function(ev) {{
          const btn = ev.target.closest(".lib-remove-btn");
          if (btn) {{
            post("/api/library/unsave", {{paper_id: btn.dataset.paperId}})
              .then(() => btn.closest(".lib-item").remove());
            return;
          }}
          const sync = ev.target.closest("#lib-notion-sync-btn");
          if (sync) {{
            sync.disabled = true;
            const statusEl = document.getElementById("lib-sync-status");
            statusEl.textContent = "Syncing to Notion…";
            fetch("/api/library/notion-sync", {{method: "POST"}})
              .then(r => r.json())
              .then(res => {{
                sync.disabled = false;
                statusEl.textContent = res.ok
                  ? `Synced: ${{res.created}} papers + ${{res.topics_created}} topics added, ${{res.updated + res.topics_updated}} updated.` +
                    (res.status_pulled ? ` ${{res.status_pulled}} status pulled from Notion.` : "")
                  : (res.detail || `Sync failed (${{res.failed || "?"}} errors)`);
              }});
          }}
          const answer = ev.target.closest("#lib-notion-answer-btn");
          if (answer) {{
            answer.disabled = true;
            const statusEl = document.getElementById("lib-sync-status");
            statusEl.textContent = "Checking comments…";
            fetch("/api/library/notion-answer-comments", {{method: "POST"}})
              .then(r => r.json())
              .then(res => {{
                answer.disabled = false;
                statusEl.textContent = res.answered
                  ? `Replied to ${{res.answered}} comment thread(s) in Notion.`
                  : (res.detail || "No unanswered comments.");
              }});
          }}
        }});
      }})();
    </script>"""
    return HTMLResponse(_library_shell(content))
