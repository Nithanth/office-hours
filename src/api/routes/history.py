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
  <title>My Reports · Lux Scholar</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/forms.css">
  <style>
    .history-wrap{{max-width:900px;margin:2rem auto;padding:0 1rem;}}
    .history-item{{border:1px solid #444;border-radius:8px;padding:1rem;margin:0.75rem 0;background:var(--container-bg);}}
    .history-meta{{font-size:0.85rem;color:var(--fg-muted,#888);margin-top:0.35rem;}}
  </style>
</head>
<body>
  <main class="history-wrap">
    <p><a href="/">← Back to search</a></p>
    <h1>My Reports</h1>
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
        items.append(
            "<article class='history-item'>"
            f"<h2><a href='/reports/{report.id}'>{html.escape(report.title)}</a></h2>"
            f"<div class='history-meta'>{html.escape(report.created_at.isoformat())} · "
            f"{html.escape(report.audience_type)} · {html.escape(report.model_key)}</div>"
            f"<p>{html.escape(report.report_text[:280])}…</p>"
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
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head>
  <title>{html.escape(report.title)} · Lux Scholar</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <script src="/static/js/report.js" defer></script>
</head>
<body>
  <main style="max-width:1000px;margin:2rem auto;padding:0 1rem;">
    <p><a href="/reports">← My Reports</a></p>
    <div id="report-content" data-report-id="{html.escape(report.id)}">
      {report_html}
    </div>
  </main>
</body>
</html>"""
    )


@router.get("/api/reports")
async def reports_api(request: Request) -> JSONResponse:
    user_id = require_auth_user_id(request)
    if not database_available():
        return JSONResponse({"ok": False, "reports": [], "detail": "Report history not configured"})

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
