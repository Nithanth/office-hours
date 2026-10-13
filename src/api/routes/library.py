"""Persistent library: save papers, track reading status, sync to Notion."""

from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services.auth.session import get_auth_user_id
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import SavedPaper
from services.persistence.repositories import (
    get_notion_integration,
    get_report,
    list_saved_paper_ids,
    list_saved_papers,
    notion_access_token,
    save_paper,
    set_notion_databases,
    set_report_notion_page,
    set_saved_paper_notion_page,
    set_saved_paper_notion_status,
    set_saved_paper_status,
    unsave_paper,
)

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
    async for session in session_scope():
        removed = await unsave_paper(session, owner_id=owner_id, paper_id=paper_id)
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

    from services.notion.library_sync import provision_databases, sync_library
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
            paper_rows = [
                {
                    "paper_id": p.paper_id,
                    "title": p.title,
                    "url": p.url,
                    "authors": p.authors,
                    "status": p.status,
                    "report_id": p.report_id,
                    "notion_page_id": p.notion_page_id,
                    "notion_synced_status": p.notion_synced_status,
                    "saved_date": p.created_at.date().isoformat(),
                }
                for p in papers
            ]
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
                        }

        stats = await sync_library(
            exporter,
            papers_db_id=papers_db_id,
            topics_db_id=topics_db_id,
            papers=paper_rows,
            reports=reports,
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

    return JSONResponse(
        {
            "ok": stats["failed"] == 0,
            "created": stats["created"],
            "updated": stats["updated"],
            "topics_created": stats["topics_created"],
            "topics_updated": stats["topics_updated"],
            "status_pulled": len(stats["status_updates"]),
            "failed": stats["failed"],
            "errors": stats["errors"][:5],
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
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Library · Office Hours</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/forms.css">
  <link rel="stylesheet" href="/static/css/modal.css">
  <link rel="stylesheet" href="/static/css/chat.css">
  <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js"></script>
  <script src="/static/js/library.js?v=1" defer></script>
  <style>
    .lib-wrap{{max-width:900px;margin:2rem auto;padding:0 1rem;}}
    .lib-header{{display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;}}
    .lib-header h1{{margin:0;}}
    .lib-header a,.lib-header button{{font-size:0.85rem;margin-left:auto;}}
    .lib-tabs{{display:flex;gap:0.5rem;margin-bottom:1.25rem;}}
    .lib-tabs a{{font-size:0.8rem;padding:3px 12px;border:1px solid var(--container-border,#555);
      border-radius:999px;text-decoration:none;color:var(--fg);}}
    .lib-tabs a.active{{background:var(--fg);color:var(--bg);}}
    .lib-item{{border:1px solid var(--container-border,#444);border-radius:8px;padding:0.9rem 1rem;
      margin:0.75rem 0;background:var(--container-bg);}}
    .lib-item h2{{font-size:1.02rem;margin:0 0 0.3rem;}}
    .lib-item h2 a{{text-decoration:none;color:var(--fg);}}
    .lib-item h2 a:hover{{text-decoration:underline;}}
    .lib-meta{{font-size:0.8rem;color:var(--fg-muted,#888);}}
    .lib-actions{{display:flex;gap:0.5rem;align-items:center;margin-top:0.6rem;flex-wrap:wrap;}}
    .lib-actions select,.lib-actions button{{
      font-size:0.78rem;padding:3px 9px;border-radius:4px;cursor:pointer;
      border:1px solid var(--container-border,#555);background:var(--container-bg,#1a1a1a);
      color:var(--fg);}}
    .lib-status-chip{{font-size:0.72rem;padding:2px 8px;border-radius:999px;
      border:1px solid var(--container-border,#666);margin-left:0.5rem;color:var(--fg-muted,#aaa);}}
  </style>
</head>
<body>
  <main class="lib-wrap">
    {content_html}
  </main>
  <div id="modal-container"></div>
  <script type="module">
    import "/static/js/chat/stream.js?v=2";
    import "/static/js/chat/ui.js?v=2";
    import "/static/js/chat/events.js?v=2";
    import "/static/js/chat/snippet.js?v=1";
  </script>
</body>
</html>"""


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
    async for session in session_scope():
        papers = await list_saved_papers(session, owner_id=owner_id, status=status)
        if signed_in:
            integration = await get_notion_integration(
                session, user_id=str(get_auth_user_id(request))
            )
            notion_connected = bool(integration and integration.parent_database_id)

    tabs = [
        '<a href="/library"' + (' class="active"' if not status else "") + ">All</a>"
    ]
    for value, label in _STATUS_LABELS.items():
        active = ' class="active"' if status == value else ""
        tabs.append(f'<a href="/library?status={value}"{active}>{label}</a>')

    if notion_connected:
        sync_btn = (
            '<button id="lib-notion-sync-btn" type="button">Sync to Notion</button>'
            '<button id="lib-notion-answer-btn" type="button" '
            'title="Reply to unanswered comments on your synced paper pages">'
            "Answer comments</button>"
        )
    elif signed_in:
        sync_btn = '<a href="/settings" title="Connect Notion in Settings to sync">Connect Notion</a>'
    else:
        sync_btn = ""

    if papers:
        items = "".join(_paper_item(p) for p in papers)
    else:
        items = "<p>No saved papers yet. Hit the bookmark icon on any paper to add it here.</p>"

    content = f"""<div class="lib-header">
      <h1>Library</h1>
      {sync_btn}
      <a href="/">+ New Search</a>
    </div>
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
