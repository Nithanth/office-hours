"""Report history pages and APIs."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from services.auth.session import require_auth_user_id
from services.persistence.database import database_available, session_scope

router = APIRouter(tags=["history"])


def _history_shell(items_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>My Reports · Office Hours</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/forms.css">
  <link rel="stylesheet" href="/static/css/modal.css">
  <link rel="stylesheet" href="/static/css/chat.css">
  <style>
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
  </style>
</head>
<body>
  <main class="history-wrap">
    <div class="history-header">
      <h1>My Reports</h1>
      <a href="/" style="font-size:0.85rem;margin-left:auto;">+ New Search</a>
    </div>
    {items_html}
  </main>
</body>
</html>"""


@router.get("/reports", response_class=HTMLResponse)
async def report_history(request: Request) -> HTMLResponse:
    user_id = require_auth_user_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Report history is not configured.")

    from services.persistence.repositories import list_reports

    async for session in session_scope():
        reports = await list_reports(session, user_id=user_id)

    if not reports:
        return HTMLResponse(_history_shell("<p>No saved reports yet.</p>"))

    items = []
    for report in reports:
        date_str = report.created_at.strftime("%b %d, %Y")
        preview = html.escape(report.report_text[:200]).rstrip() + "…"
        items.append(
            "<article class='history-item'>"
            f"<h2><a href='/reports/{report.id}'>{html.escape(report.title)}</a></h2>"
            f"<div class='history-meta'>{date_str} · "
            f"{html.escape(report.audience_type)} · {html.escape(report.model_key)}</div>"
            f"<div class='history-preview'>{preview}</div>"
            "</article>"
        )
    return HTMLResponse(_history_shell("".join(items)))


@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(request: Request, report_id: str) -> HTMLResponse:
    user_id = require_auth_user_id(request)
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
    escaped_title = html.escape(report.title)
    escaped_id = html.escape(report.id)
    escaped_audience = html.escape(report.audience_type or "technical")
    escaped_model = html.escape(report.model_key or "")
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head>
  <title>{escaped_title} · Office Hours</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/forms.css">
  <link rel="stylesheet" href="/static/css/modal.css">
  <link rel="stylesheet" href="/static/css/chat.css">
  <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js"></script>
  <script src="/static/js/report.js" defer></script>
  <script src="/static/js/library.js?v=1" defer></script>
  <style>
    .report-actions{{display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;
      padding:0.75rem 0;border-bottom:1px solid #444;margin-bottom:1rem;}}
    .report-actions button,.report-actions select{{
      font-size:0.8rem;padding:4px 10px;border-radius:4px;cursor:pointer;
      border:1px solid #555;background:var(--container-bg,#1a1a1a);color:var(--fg);}}
    .report-actions button:hover{{background:#333;}}
    .report-meta{{font-size:0.8rem;color:var(--fg-muted,#888);margin-bottom:0.5rem;}}
  </style>
</head>
<body>
  <main style="max-width:1000px;margin:2rem auto;padding:0 1rem;">
    <p><a href="/reports">&larr; My Reports</a> · <a href="/">New Search</a></p>
    <h1 style="font-size:1.3rem;margin:0.5rem 0;">{escaped_title}</h1>
    <div class="report-meta">
      {html.escape(report.created_at.strftime('%b %d, %Y'))} &middot;
      {escaped_audience} &middot; {escaped_model}
    </div>
    <div class="report-actions">
      <select id="notes-fmt-select">
        <option value="md">Markdown</option>
        <option value="txt">Plain Text</option>
        <option value="docx">Word (.docx)</option>
      </select>
      <button onclick="window.__exportNotes('{escaped_id}')">Export Notes</button>
      <button onclick="window.__exportNotion('{escaped_id}')">Export to Notion</button>
      <button onclick="window.__generateBlog('{escaped_id}')">Generate Blog</button>
      <button hx-get="/viewer/topic?report_id={escaped_id}&title=Ask%20about%20this%20topic"
              hx-target="#modal-container" hx-swap="innerHTML">Ask about this topic</button>
    </div>
    <div id="report-content" data-report-id="{escaped_id}"
         data-audience="{escaped_audience}" data-model="{escaped_model}">
      {report_html}
    </div>
    <div id="modal-container"></div>
    <script type="module">
      import "/static/js/chat/stream.js?v=2";
      import "/static/js/chat/ui.js?v=2";
      import "/static/js/chat/events.js?v=2";
      import "/static/js/chat/snippet.js?v=1";
    </script>
  </main>
</body>
</html>"""
    )


@router.get("/api/reports")
async def reports_api(request: Request) -> JSONResponse:
    user_id = require_auth_user_id(request)
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
                    "title": report.title,
                    "created_at": report.created_at.isoformat(),
                    "audience_type": report.audience_type,
                    "model_key": report.model_key,
                }
                for report in reports
            ],
        }
    )
