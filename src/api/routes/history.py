"""Report history pages and APIs."""

from __future__ import annotations

import html
import re

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


def _model_label(report: Report) -> str:
    """User-facing model name — tier display names, not registry keys."""
    import config as _cfg

    key = _cfg.config.resolve_model_key(report.model_key)
    if key and key in _cfg.config.AVAILABLE_MODELS:
        return str(_cfg.config.AVAILABLE_MODELS[key]["display_name"])
    return report.model_key or ""


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
            + f"""<div class="lib-header">
      <h1>My reports</h1>
    </div>
    {items_html}""",
            active="library",
            head_extra="""<style>
    .history-item{border:1px solid var(--container-border,#444);border-radius:8px;padding:1rem;margin:0.75rem 0;
      background:var(--container-bg);transition:border-color 0.15s;}
    .history-item:hover{border-color:var(--fg-muted,#888);}
    .history-item h2{font-size:1.05rem;margin:0 0 0.3rem;}
    .history-item h2 a{text-decoration:none;color:var(--fg);}
    .history-item h2 a:hover{text-decoration:underline;}
    .history-meta{font-size:0.8rem;color:var(--fg-muted,#888);margin-top:0.25rem;}
    .history-meta a{color:var(--fg-muted,#888);}
    .history-preview{font-size:0.88rem;color:var(--fg-muted,#aaa);margin-top:0.4rem;line-height:1.4;}
  </style>""",
            wrap_class="page-wrap",
        )
    )


@router.get("/reports", response_class=HTMLResponse)
async def report_history(request: Request) -> HTMLResponse:
    user_id = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Report history is not configured.")

    from services.persistence.repositories import list_reports, list_topics

    async for session in session_scope():
        reports = await list_reports(session, user_id=user_id)
        topics_by_id = {
            t.id: t.title for t in await list_topics(session, owner_id=user_id)
        }

    if not reports:
        return HTMLResponse(
            _history_shell(
                '<div class="empty-state-card"><strong>No saved reports yet.</strong>'
                "<p>Run a search and generate a report. Your snapshots land here.</p>"
                '<a href="/">New search</a></div>'
            )
        )

    items = []
    for report in reports:
        date_str = report.created_at.strftime("%b %d, %Y")
        # Strip markdown/html scaffolding so previews read as prose.
        raw = re.sub(r"<[^>]+>", " ", report.report_text or "")
        raw = re.sub(r"^#{1,6}\s*", "", raw, flags=re.M)
        raw = re.sub(r"\s+", " ", raw).strip()
        preview = html.escape(raw[:200]).rstrip() + "…"
        topic_bit = ""
        if report.topic_id and report.topic_id in topics_by_id:
            topic_bit = (
                f' · in <a href="/topics/{html.escape(report.topic_id)}">'
                f"{html.escape(topics_by_id[report.topic_id])}</a>"
            )
        items.append(
            "<article class='history-item'>"
            f"<h2><a href='/reports/{report.id}'>{html.escape(_display_title(report))}</a></h2>"
            f"<div class='history-meta'>{date_str} · "
            f"{html.escape(report.audience_type)} · {html.escape(_model_label(report))}"
            f"{topic_bit}</div>"
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
        topic_title = None
        if report.topic_id:
            from services.persistence.repositories import get_topic

            t = await get_topic(session, owner_id=user_id, topic_id=report.topic_id)
            topic_title = t.title if t is not None else None

    report_html = report.report_html.replace(
        "hx-get='/chat-modal?",
        f"hx-get='/chat-modal?report_id={html.escape(report.id)}&",
    )
    escaped_title = html.escape(_display_title(report))
    escaped_id = html.escape(report.id)
    escaped_audience = html.escape(report.audience_type or "technical")
    escaped_model = html.escape(_model_label(report))
    topic_bit = ""
    if report.topic_id and topic_title:
        topic_bit = (
            f' &middot; in <a href="/topics/{html.escape(report.topic_id)}">'
            f"{html.escape(topic_title)}</a>"
        )
    from pages.shell import lib_subtabs_html, page_shell

    return HTMLResponse(
        page_shell(
            _display_title(report),
            lib_subtabs_html("reports")
            + f"""
    <h1 style="font-size:1.3rem;margin:0.5rem 0;">{escaped_title}</h1>
    <div class="report-meta">
      {html.escape(report.created_at.strftime('%b %d, %Y'))} &middot;
      {escaped_audience} &middot; {escaped_model}{topic_bit}
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
      <button id="report-notes-btn" type="button" class="btn-secondary">Export Notes</button>
      <button class="btn-secondary" hx-get="/viewer/topic?report_id={escaped_id}&title=Ask%20about%20this%20topic"
              hx-target="#modal-container" hx-swap="innerHTML">Ask about this topic</button>
    </div>
    <div id="report-content" data-report-id="{escaped_id}"
         data-audience="{escaped_audience}" data-model="{escaped_model}">
      {report_html}
    </div>
""",
            active="library",
            head_extra="""<link rel="stylesheet" href="/static/css/blog.css">
  <style>
    .report-actions{display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center;
      padding:0.75rem 0;border-bottom:1px solid var(--container-border,#444);margin-bottom:1rem;}
    .report-actions button,.report-actions select{
      font-size:0.8rem;padding:4px 10px;border-radius:4px;cursor:pointer;
      border:1px solid var(--container-border,#555);background:var(--container-bg);color:var(--fg);}
    .report-actions button:hover{background:var(--oh-accent-soft,#333);}
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
