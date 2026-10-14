"""Topic pages and APIs — the topic is the unit; papers are its contents."""

from __future__ import annotations

import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.topics.keys import canonical_key_for, detect_topic_kind

router = APIRouter(tags=["topics"])


def _owner_id(request: Request) -> str:
    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Sign in to view topics.")
    return str(owner)


def _shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>{html.escape(title)} · Office Hours</title>
  <link rel="stylesheet" href="/static/css/base.css">
  <link rel="stylesheet" href="/static/css/forms.css">
  <link rel="stylesheet" href="/static/css/modal.css">
  <link rel="stylesheet" href="/static/css/chat.css">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <script src="https://cdn.jsdelivr.net/npm/htmx.org@1.9.10/dist/ext/sse.js"></script>
  <script src="/static/js/report.js?v=2" defer></script>
  <script src="/static/js/main.js?v=2" type="module"></script>
  <style>
    .topics-wrap{{max-width:900px;margin:2rem auto;padding:0 1rem;}}
    .topic-card{{border:1px solid #444;border-radius:8px;padding:1rem;margin:0.75rem 0;
      background:var(--container-bg);transition:border-color 0.15s;}}
    .topic-card:hover{{border-color:#888;}}
    .topic-card h2{{font-size:1.05rem;margin:0 0 0.3rem;}}
    .topic-card h2 a{{text-decoration:none;color:var(--fg);}}
    .topic-card h2 a:hover{{text-decoration:underline;}}
    .topic-meta{{font-size:0.8rem;color:var(--fg-muted,#888);margin-top:0.25rem;}}
    .topic-kind{{display:inline-block;font-size:0.7rem;padding:0.1rem 0.45rem;
      border:1px solid #555;border-radius:999px;margin-right:0.4rem;text-transform:uppercase;}}
    .paper-row{{display:flex;justify-content:space-between;gap:0.75rem;
      border:1px solid #444;border-radius:8px;padding:0.75rem;margin:0.5rem 0;
      background:var(--container-bg);}}
    .paper-row .pt a{{color:var(--fg);font-weight:600;}}
    .paper-row .pm{{font-size:0.78rem;color:var(--fg-muted,#888);margin-top:0.2rem;}}
    .topic-actions a,.topic-actions button{{margin-right:0.5rem;}}
    .empty-note{{color:var(--fg-muted,#888);padding:2rem 0;text-align:center;}}
  </style>
</head>
<body>
  <div class="topics-wrap">
    <div style="margin-bottom:1rem;"><a href="/" style="text-decoration:none;">&larr; Office Hours</a></div>
    {body}
  </div>
</body>
</html>"""


def _esc(v: object) -> str:
    return html.escape(str(v or ""))


def _fmt_authors(authors: object) -> str:
    if isinstance(authors, dict):
        authors = authors.get("items") or []
    if isinstance(authors, list):
        return ", ".join(_esc(a) for a in authors[:4] if a)
    return _esc(authors)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/topics", response_class=HTMLResponse)
async def topics_page(request: Request) -> HTMLResponse:
    """Topics index — every subject/paper/author you follow."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import list_topics

    async for session in session_scope():
        topics = await list_topics(session, owner_id=owner)

    if not topics:
        items = "<div class='empty-note'>No topics yet — run a search and generate a report to start one.</div>"
    else:
        rows = []
        for t in topics:
            rows.append(
                f"""<div class="topic-card">
  <h2><a href="/topics/{_esc(t.id)}">{_esc(t.title)}</a></h2>
  <div class="topic-meta"><span class="topic-kind">{_esc(t.kind)}</span>
    started {t.created_at.strftime("%b %d, %Y")}</div>
</div>"""
            )
        items = "".join(rows)
    return HTMLResponse(_shell("Topics", f"<h1>Your Topics</h1>{items}"))


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
async def topic_page(topic_id: str, request: Request) -> HTMLResponse:
    """One topic: its paper set, latest report snapshot, and actions."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import (
        get_topic,
        list_topic_papers,
        list_topic_reports,
    )

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        papers = await list_topic_papers(session, topic_id=topic.id)
        reports = await list_topic_reports(session, topic_id=topic.id, limit=5)

    paper_rows = []
    for p in papers:
        title_link = (
            f'<a href="{_esc(p.url)}" target="_blank" rel="noopener">{_esc(p.title)}</a>'
            if p.url
            else _esc(p.title)
        )
        meta_bits = [b for b in (_fmt_authors(p.authors), _esc(p.published_date)) if b]
        meta = f"<div class='pm'>{' · '.join(meta_bits)}</div>" if meta_bits else ""
        paper_rows.append(
            f"""<div class="paper-row">
  <div><div class="pt">{title_link}</div>{meta}</div>
  <button type="button" class="copy-icon-btn" title="Remove from topic"
    hx-post="/api/topics/{_esc(topic.id)}/papers/{quote(p.paper_id, safe='')}/remove"
    hx-target="closest .paper-row" hx-swap="outerHTML">&times;</button>
</div>"""
        )
    papers_html = "".join(paper_rows) or "<div class='empty-note'>No papers yet.</div>"

    report_rows = []
    for r in reports:
        report_rows.append(
            f"<div class='topic-meta'>&#8226; <a href='/reports/{_esc(r.id)}'>"
            f"{_esc(r.title)}</a> — {r.created_at.strftime('%b %d, %Y')}</div>"
        )
    reports_html = (
        "".join(report_rows) or "<div class='topic-meta'>No reports yet.</div>"
    )

    body = f"""<h1>{_esc(topic.title)} <span class="topic-kind">{_esc(topic.kind)}</span></h1>
<div class="topic-actions" style="margin:0.75rem 0;">
  <a class="btn-secondary" href="/" style="text-decoration:none;">+ New report</a>
  <button type="button" class="btn-secondary" title="Re-render the report against this topic's current papers"
    hx-post="/topics/{_esc(topic.id)}/report" hx-target="#topic-report-out"
    hx-swap="innerHTML" hx-disabled-elt="this">Refresh report</button>
  <button type="button" class="btn-secondary"
    hx-get="/chat-modal?paper_id=topic&topic_id={_esc(topic.id)}&title={quote('Chat about this topic')}"
    hx-target="#modal-container" hx-swap="innerHTML">Topic chat</button>
</div>
<div id="topic-report-out"></div>
<div id="modal-container"></div>
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Reports</h2>
{reports_html}
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Papers ({len(papers)})</h2>
{papers_html}"""
    return HTMLResponse(_shell(topic.title, body))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@router.get("/api/topics")
async def api_list_topics(request: Request) -> JSONResponse:
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import list_topics

    async for session in session_scope():
        topics = await list_topics(session, owner_id=owner)
    return JSONResponse(
        [
            {
                "id": t.id,
                "title": t.title,
                "kind": t.kind,
                "canonical_key": t.canonical_key,
                "created_at": t.created_at.isoformat(),
            }
            for t in topics
        ]
    )


@router.post("/api/topics")
async def api_create_topic(request: Request, query: str = Form(...)) -> JSONResponse:
    """Create-or-join a topic from a query string (subject/paper/author)."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import get_or_create_topic

    kind = detect_topic_kind(query)
    ckey = canonical_key_for(query, kind=kind)
    if not ckey:
        raise HTTPException(status_code=400, detail="Empty query")
    async for session in session_scope():
        topic, created = await get_or_create_topic(
            session,
            owner_id=owner,
            kind=kind,
            canonical_key=ckey,
            title=query,
            seed_query=query,
        )
        await session.commit()
    return JSONResponse(
        {"id": topic.id, "kind": topic.kind, "created": created},
        status_code=201 if created else 200,
    )


@router.post("/topics/{topic_id}/report", response_class=HTMLResponse)
async def topic_rerender(topic_id: str, request: Request) -> HTMLResponse:
    """Re-render a report snapshot against the topic's current paper set.

    Builds the same payload the generate form would submit (papers = the
    topic's set, query = the topic's seed), then returns the SSE skeleton —
    the stream links the new snapshot to the topic.
    """
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import get_topic, list_topic_papers

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        rows = await list_topic_papers(session, topic_id=topic.id)

    if not rows:
        raise HTTPException(
            status_code=400, detail="This topic has no papers to synthesize."
        )

    # report_stream expects one Paper-JSON string per list element —
    # Paper.model_validate_json per item.
    papers_json_items = [
        json.dumps(
            {
                "id": r.paper_id,
                "title": r.title,
                "url": r.url or "",
                "authors": (
                    (r.authors or {}).get("items", [])
                    if isinstance(r.authors, dict)
                    else (r.authors or [])
                ),
                "published_date": r.published_date or "",
            }
        )
        for r in rows
    ]

    from api.routers.report_stream import (
        _report_skeleton_html,
        cache_report_request,
    )

    token = cache_report_request(
        {
            "original_query": topic.seed_query or topic.title,
            "selected_papers_json": papers_json_items,
            "audience_type": "technical",
            "summary_size": "M",
            "selected_model_key": "",
            "is_url_input": topic.kind == "paper",
            "user_id": owner,
            "topic_id": topic.id,
        }
    )
    return HTMLResponse(_report_skeleton_html(token))


@router.post("/api/topics/{topic_id}/papers/{paper_id:path}/remove")
async def api_remove_paper(topic_id: str, paper_id: str, request: Request) -> Response:
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import get_topic, remove_topic_paper

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        removed = await remove_topic_paper(
            session, topic_id=topic_id, paper_id=paper_id
        )
    if request.headers.get("hx-request"):
        return HTMLResponse("", status_code=200)
    return JSONResponse({"ok": True, "removed": removed})
