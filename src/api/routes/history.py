"""Report history pages and APIs."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.routers.report_stream import _clean_generated_title
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import Report


def _display_title(report: Report) -> str:
    """Render-time title cleanup — scrubs corrupt persisted titles (e.g.
    truncated fragments like '(their') so old rows self-heal."""
    return (
        _clean_generated_title(report.title)
        or report.original_query
        or "Untitled report"
    )


router = APIRouter(tags=["history"])


def _owner_id(request: Request) -> str:
    """Report owner key: auth user_id, else the signed session id.

    ChatSessionMiddleware guarantees every request has a session, so
    anonymous visitors list their own reports here rather than 401ing.
    """
    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Sign in to view reports.")
    return str(owner)


def _history_shell(items_html: str) -> str:
    from pages.shell import lib_subtabs_html, page_shell

    return str(
        page_shell(
            "My Reports",
            lib_subtabs_html("reports")
            + f"""<div class="history-header">
      <h1>My Reports</h1>
      <a href="/" style="font-size:0.85rem;margin-left:auto;">+ New Search</a>
    </div>
    {items_html}""",
            active="library",
            head_extra="""<style>
    .history-wrap{{max-width:900px;margin:2rem auto;padding:0 1rem;}}
    .history-item{{border:1px solid #444;border-radius:8px;padding:1rem;margin:0.75rem 0;
      background:var(--container-bg);transition:border-color 0.15s;}}
    .history-item:hover{{border-color:#888;}}
    .history-item h2{{font-size:1.05rem;margin:0 0 0.3rem;}}
    .history-item h2 a{{text-decoration:none;color:var(--fg);}}
    .history-item h2 a:hover{{text-decoration:underline;}}
    .history-meta{{font-size:0.8rem;color:var(--fg-muted,#888);margin-top:0.25rem;}}
    .history-preview{{font-size:0.88rem;color:var(--fg-muted,#aaa);margin-top:0.4rem;line-height:1.4;}}
    .history-header{{display:flex;align-items:center;gap:1rem;margin-bottom:1.5rem;}}
    .history-header h1{{margin:0;}}
  </style>""",
            wrap_class="history-wrap",
        )
    )


@router.get("/reports", response_class=HTMLResponse)
async def report_history(request: Request) -> HTMLResponse:
    user_id = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Report history is not configured.")

    from services.persistence.repositories import list_reports

    async for session in session_scope():
        reports = await list_reports(session, user_id=user_id)

    if not reports:
        return HTMLResponse(
            _history_shell(
                '<div class="empty-state-card"><strong>No saved reports yet.</strong>'
                "<p>Run a search and generate a report — your snapshots land here.</p>"
                '<a href="/">New search</a></div>'
            )
        )

    items = []
    for report in reports:
        date_str = report.created_at.strftime("%b %d, %Y")
        preview = html.escape(report.report_text[:200]).rstrip() + "…"
        items.append(
            "<article class='history-item'>"
            f"<h2><a href='/reports/{report.id}'>{html.escape(_display_title(report))}</a></h2>"
            f"<div class='history-meta'>{date_str} · "
            f"{html.escape(report.audience_type)} · {html.escape(report.model_key)}</div>"
            f"<div class='history-preview'>{preview}</div>"
            "</article>"
        )
    return HTMLResponse(_history_shell("".join(items)))


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(request: Request, report_id: str) -> HTMLResponse:
    user_id = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Report history is not configured.")

    from services.persistence.repositories import get_report

    async for session in session_scope():
        report = await get_report(session, user_id=user_id, report_id=report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    report_html = report.report_html.replace(
        "hx-get='/chat-modal?",
        f"hx-get='/chat-modal?report_id={html.escape(report.id)}&",
    )
    escaped_title = html.escape(_display_title(report))
    escaped_id = html.escape(report.id)
    escaped_audience = html.escape(report.audience_type or "technical")
    escaped_model = html.escape(report.model_key or "")
    from pages.shell import page_shell

    return HTMLResponse(
        page_shell(
            _display_title(report),
            f"""
    <h1 style="font-size:1.3rem;margin:0.5rem 0;">{escaped_title}</h1>
    <div class="report-meta">
      {html.escape(report.created_at.strftime('%b %d, %Y'))} &middot;
      {escaped_audience} &middot; {escaped_model}
    </div>
    <div id="report-view-toggle" class="oh-view-toggle">
      <button type="button" data-view="report" class="active"><i class="fa-regular fa-file-lines"></i> Report</button>
      <button type="button" data-view="blog"><i class="fa-solid fa-pen-nib"></i> Blog</button>
    </div>
    <div class="report-actions">
      <select id="notes-fmt-select">
        <option value="md">Markdown</option>
        <option value="txt">Plain Text</option>
        <option value="docx">Word (.docx)</option>
      </select>
      <button id="report-notes-btn" type="button">Export Notes</button>
      <button id="report-notion-btn" type="button">Export to Notion</button>
      <button hx-get="/viewer/topic?report_id={escaped_id}&title=Ask%20about%20this%20topic"
              hx-target="#modal-container" hx-swap="innerHTML">Ask about this topic</button>
    </div>
    <div id="report-content" data-report-id="{escaped_id}"
         data-audience="{escaped_audience}" data-model="{escaped_model}">
      {report_html}
    </div>
""",
            active="reports",
            head_extra="""<link rel="stylesheet" href="/static/css/blog.css">
  <style>
    .report-actions{display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;
      padding:0.75rem 0;border-bottom:1px solid #444;margin-bottom:1rem;}
    .report-actions button,.report-actions select{
      font-size:0.8rem;padding:4px 10px;border-radius:4px;cursor:pointer;
      border:1px solid #555;background:var(--container-bg,#1a1a1a);color:var(--fg);}
    .report-actions button:hover{background:#333;}
    .report-meta{font-size:0.8rem;color:var(--fg-muted,#888);margin-bottom:0.5rem;}
  </style>""",
            scripts="""<script src="/static/js/report.js" defer></script>
  <script src="/static/js/library.js?v=1" defer></script>
  <script src="/static/js/blog-modal.js" defer></script>
  <script src="/static/js/blog-view.js" defer></script>
  <script src="/static/js/blog-pane.js" defer></script>
  <script>document.addEventListener("DOMContentLoaded",function(){
      var r=window.ohBlogRender;r&&r.loadAllDeps().then(function(){
        r.renderKaTeX(document.getElementById('report-content'));});});</script>
  <script type="module">
      import "/static/js/chat/stream.js?v=2";
      import "/static/js/chat/ui.js?v=2";
      import "/static/js/chat/events.js?v=2";
      import "/static/js/chat/snippet.js?v=1";
    </script>""",
            wrap_class="report-wrap",
        )
    )


@router.get("/api/reports")
async def reports_api(request: Request) -> JSONResponse:
    user_id = _owner_id(request)
    if not database_available():
        return JSONResponse(
            {"ok": False, "reports": [], "detail": "Report history not configured"}
        )

    from services.persistence.repositories import list_reports

    async for session in session_scope():
        reports = await list_reports(session, user_id=user_id)
    return JSONResponse(
        {
            "ok": True,
            "reports": [
                {
                    "id": report.id,
                    "title": _display_title(report),
                    "created_at": report.created_at.isoformat(),
                    "audience_type": report.audience_type,
                    "model_key": report.model_key,
                }
                for report in reports
            ],
        }
    )
