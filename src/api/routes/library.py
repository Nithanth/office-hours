"""Persistent library: save papers, track reading status, sync to Notion."""

from __future__ import annotations

import asyncio
import html
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from api.deps import get_exa_client
from services.auth.session import get_auth_user_id
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import SavedPaper
from services.persistence.repositories import (
    feed_new_counts,
    get_notion_integration,
    get_report,
    get_saved_paper,
    get_topic,
    home_stats,
    list_paper_chat_messages,
    list_reports,
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


# In-process TTL cache for the for-you row — recommendation calls (Exa
# find_similar) shouldn't fire on every library render.
_foryou_cache: dict[str, tuple[float, str]] = {}


@router.get("/api/library/for-you", response_class=HTMLResponse)
async def library_for_you(request: Request) -> HTMLResponse:
    """Lazy-loaded 'for you' recommendations: similar papers to recent saves
    + authors that recur across the library. Rendered as chips."""
    import time

    owner = _owner_id(request)
    empty = HTMLResponse("<span class='oh-foryou-empty'></span>")
    if not owner or not database_available():
        return empty

    cached = _foryou_cache.get(owner)
    if cached and time.monotonic() - cached[0] < 3600:
        return HTMLResponse(cached[1])

    try:
        async for session in session_scope():
            saved = await list_saved_papers(session, owner_id=owner)
            saved_ids = await list_saved_paper_ids(session, owner_id=owner)

        # Authors: rank by how many saved papers carry them — recurring
        # names are the people whose work keeps resonating.
        counts: dict[str, int] = {}
        for p in saved:
            names = (p.authors or {}).get("list") or []
            for n in names:
                n = n.strip()
                if n:
                    counts[n] = counts.get(n, 0) + 1
        authors_html: list[str] = []
        for name, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
            if n < 2:
                continue  # one-off authors aren't a signal
            esc_name = html.escape(name)
            authors_html.append(
                f'<a class="oh-foryou-chip" href="/?search_query={esc_name}"'
                f' title="On {n} of your saved papers. Click to search.">'
                f"{esc_name} ×{n}</a>"
            )

        # Papers: Exa find_similar seeded by the 2 most recent saves w/ URLs.
        papers_html: list[str] = []
        exa = get_exa_client()
        if exa is not None:
            for seed in [p for p in saved if p.url][:2]:
                try:
                    sims = await asyncio.to_thread(exa.find_similar_papers, seed.url, 5)
                except Exception:
                    continue
                for pap in sims:
                    pap_url = str(pap.url) if pap.url else ""
                    pap_id = str(pap.id) if pap.id else ""
                    if not pap_url or pap_url in saved_ids or pap_id in saved_ids:
                        continue
                    title = (str(pap.title) if pap.title else pap_url).strip()
                    if not title:
                        continue
                    papers_html.append(
                        '<a class="oh-foryou-chip oh-foryou-paper"'
                        f' href="{html.escape(pap_url)}" target="_blank" '
                        'rel="noopener" title="Similar to one of your saves">'
                        f"{html.escape(title[:90])}</a>"
                    )
                    if len(papers_html) >= 4:
                        break
                if len(papers_html) >= 4:
                    break

        body = ""
        if authors_html:
            body += '<span class="oh-foryou-label">Authors to watch</span> ' + " ".join(
                authors_html
            )
        if papers_html:
            body += (
                ' <span class="oh-foryou-label">Papers you may like</span> '
                + " ".join(papers_html)
            )
        markup = f'<div class="oh-foryou-inner">{body}</div>' if body else ""
    except Exception:
        # Recommendations are best-effort: fail silent, never paint an error.
        logger.warning("library_for_you_failed", owner=owner)
        return empty

    _foryou_cache[owner] = (time.monotonic(), markup)
    return HTMLResponse(markup)


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


def schedule_notion_workspace_sync(owner_id: str) -> None:
    """Fire-and-forget full workspace sync — papers, topics, reports, chat
    mirrors, open comments. Used after report/blog/notes generation so new
    content lands in Notion without a manual Sync-now press. No-ops for
    users without a connected integration."""
    task = asyncio.create_task(run_notion_workspace_sync(owner_id))
    task.add_done_callback(
        lambda t: (
            logger.warning("notion_workspace_sync_failed", error=str(t.exception()))
            if t.exception()
            else None
        )
    )


async def run_notion_workspace_sync(user_id: str) -> dict | None:
    """Full workspace Notion sync for one user; returns the stats payload.

    No-ops (returns None) when the DB is unavailable or the user has no
    connected Notion integration."""
    if not database_available():
        return None
    integration, token = await _load_integration(str(user_id))
    if integration is None or not token:
        return None

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
        # Every report the user owns — not just ones linked from a saved
        # paper — gets a row in the Topics DB. Generation is the moment a
        # report should exist in Notion, not when it's saved to the library.
        reports: dict = {}
        async for session in session_scope():
            for report in await list_reports(session, user_id=str(user_id), limit=200):
                reports[report.id] = {
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

        # One button, full loop: also answer any unanswered comments on the
        # synced paper pages — no separate "Answer comments" action.
        from services.notion.library_sync import answer_comments_for_paper

        comments_answered = 0
        for row in paper_rows:
            page_id = (
                stats["paper_page_ids"].get(row["paper_id"]) or row["notion_page_id"]
            )
            if not page_id:
                continue
            try:
                comments_answered += await answer_comments_for_paper(
                    exporter,
                    notion_page_id=str(page_id),
                    paper_id=row["paper_id"],
                    user_id=str(user_id),
                    bot_id=integration["bot_id"],
                )
            except Exception as exc:
                stats["errors"].append(f"comments {row['paper_id']}: {exc}"[:200])
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

    return {
        "ok": stats["failed"] == 0 and ws_stats["failed"] == 0,
        "created": stats["created"],
        "updated": stats["updated"],
        "topics_created": stats["topics_created"] + ws_stats["topics_created"],
        "topics_updated": stats["topics_updated"] + ws_stats["topics_updated"],
        "topic_bodies_written": ws_stats["bodies_written"],
        "status_pulled": len(stats["status_updates"]),
        "comments_answered": comments_answered,
        "failed": stats["failed"] + ws_stats["failed"],
        "errors": (stats["errors"] + ws_stats["errors"])[:5],
    }


@router.post("/api/library/notion-sync")
async def library_notion_sync(request: Request) -> JSONResponse:
    user_id = get_auth_user_id(request)
    if not user_id:
        return JSONResponse(
            {"ok": False, "detail": "Sign in to sync your library to Notion."},
            status_code=401,
        )
    payload = await run_notion_workspace_sync(str(user_id))
    if payload is None:
        return JSONResponse(
            {
                "ok": False,
                "detail": "Connect Notion first (Settings → Notion).",
            },
            status_code=400,
        )
    return JSONResponse(payload)


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
    from pages.shell import lib_subtabs_html, page_shell

    return str(
        page_shell(
            "Library",
            lib_subtabs_html("papers") + content_html,
            active="library",
            scripts=f"""<script src="{static_url('/static/js/library.js')}" defer></script>
  <script src="{static_url('/static/js/cluster-viz.js')}" defer></script>
  <script type="module">
    import "{static_url('/static/js/chat/stream.js')}";
    import "{static_url('/static/js/chat/ui.js')}";
    import "{static_url('/static/js/chat/events.js')}";
    import "{static_url('/static/js/chat/snippet.js')}";
  </script>""",
            wrap_class="page-wrap",
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
    <button class="btn-secondary" hx-get="{viewer_url}" hx-target="#modal-container" hx-swap="innerHTML">Read &amp; chat</button>
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
    stats: dict[str, int] = {}
    recent_topics: list = []
    new_counts: dict[str, int] = {}
    async for session in session_scope():
        papers = await list_saved_papers(session, owner_id=owner_id, status=status)
        stats = await home_stats(session, owner_id=owner_id)
        recent_topics = await list_topics(session, owner_id=owner_id, limit=1)
        new_counts = await feed_new_counts(
            session, topic_ids=[t.id for t in recent_topics]
        )
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
            '<button id="lib-notion-sync-btn" class="btn-secondary oh-tip" type="button" '
            'data-tip="One shot: re-push every saved paper + topic to Notion and reply to '
            "unanswered comments on your synced pages. Sync already runs automatically "
            'when you save or change status. Use this only if Notion drifted">'
            "Sync now</button>"
        )
        notion_card = ""
    elif notion_needs_destination:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Notion connected.</strong> One last step: pick where your library lives.
  <div class="lib-notion-pick">
    <select id="lib-notion-destination"><option value="">Loading your pages…</option></select>
    <button id="lib-notion-set-dest" type="button" class="btn-secondary">Set up sync</button>
  </div>
  <div class="lib-notion-hint">Office Hours creates two linked databases, Papers ↔ Topics, under the page you pick.</div>
</div>"""
    elif signed_in:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Mirror your library in Notion</strong>. Papers, topics and reading status sync automatically, and the agent answers questions left in page comments.
  <div class="lib-notion-pick"><a class="lib-notion-connect" href="/integrations/notion/connect?next=/library">Connect Notion</a></div>
</div>"""
    else:
        sync_btn = ""
        notion_card = """<div class="lib-notion-card">
  <strong>Mirror your library in Notion</strong>. Sign in to connect your workspace.
</div>"""

    if papers:
        items = "".join(_paper_item(p) for p in papers)
    elif status:
        items = (
            '<div class="empty-state-card"><strong>Nothing here yet.</strong>'
            "<p>No papers with this status. Pick another tab above, or add "
            "papers from a search.</p></div>"
        )
    else:
        items = (
            '<div class="empty-state-card"><strong>Your library is empty.</strong>'
            "<p>Save papers from search results with the bookmark icon and "
            "they'll land here. Anonymous sessions keep a library too.</p>"
            '<a href="/">Search papers</a></div>'
        )

    # Clustering only makes sense with real mass — a handful of papers
    # produces the same two meaningless blobs as a single search did.
    viz_btn = ""
    if len(papers) >= 4:
        viz_btn = (
            '<button id="cluster-viz-btn" class="oh-tip" type="button" '
            'data-scope="library" '
            'data-tip="Scatter of your saved papers, grouped by topic similarity'
            '. Dot size is citation count. Click a point to jump to the paper.">'
            '<i class="fa-solid fa-diagram-project"></i> Visualize</button>'
        )

    # "Your research" surface — only when the owner has data to surface.
    surface_card = ""
    if stats.get("saved") or recent_topics:
        bits = []
        if stats.get("saved"):
            bits.append(f"{stats['saved']} saved")
        if stats.get("done"):
            bits.append(f"{stats['done']} read")
        if stats.get("to_read"):
            bits.append(f"{stats['to_read']} to read")
        if stats.get("topics"):
            bits.append(f"{stats['topics']} topics")
        if stats.get("reports"):
            bits.append(f"{stats['reports']} reports")
        cont = ""
        if recent_topics:
            top = recent_topics[0]
            n = new_counts.get(top.id, 0)
            badge = (
                f'<span class="oh-surface-badge"> +{n} this week</span>' if n else ""
            )
            cont = (
                '<div class="oh-surface-continue">'
                '<span class="oh-surface-cont-label">Continue: </span>'
                f'<a class="oh-surface-cont-link" href="/topics/{top.id}">'
                f"{html.escape(top.title)}</a>{badge}</div>"
            )
        surface_card = f"""<div class="oh-surface">
  <div class="oh-surface-title">Your research</div>
  <div class="oh-surface-stats">{" · ".join(bits)}</div>
  {cont}
  <div id="oh-for-you" class="oh-surface-foryou"
    hx-get="/api/library/for-you" hx-trigger="load" hx-swap="innerHTML"></div>
</div>"""

    content = f"""<div class="lib-header">
      <h1>My saved papers</h1>
      {sync_btn}
      {viz_btn}
    </div>
    {notion_card}
    {surface_card}
    <div class="lib-tabs">{"".join(tabs)}</div>
    <div id="cluster-viz-container" style="display:none;margin:12px 0;"></div>
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
              : '<option value="">No shared pages. Re-connect and share a page</option>';
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
                statusEl.textContent = "All set. Papers now sync to Notion automatically.";
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
                    (res.status_pulled ? ` ${{res.status_pulled}} status pulled from Notion.` : "") +
                    (res.comments_answered ? ` ${{res.comments_answered}} comment thread(s) answered.` : "")
                  : (res.detail || `Sync failed (${{res.failed || "?"}} errors)`);
              }});
          }}
        }});
      }})();
    </script>"""
    return HTMLResponse(_library_shell(content))
