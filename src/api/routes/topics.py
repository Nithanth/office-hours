"""Topic pages and APIs — the topic is the unit; papers are its contents."""

from __future__ import annotations

import html
import json
import re
import secrets
from urllib.parse import quote, quote_plus

import markdown as _markdown
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from api.routers.report_stream import _clean_generated_title
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import Report, Topic, TopicPaper
from services.topics.keys import canonical_key_for, detect_topic_kind


def _display_report_title(report: Report) -> str:
    """Render-time cleanup of corrupt persisted titles (self-heals old rows)."""
    return (
        _clean_generated_title(report.title)
        or report.original_query
        or "Untitled report"
    )


router = APIRouter(tags=["topics"])


def _owner_id(request: Request) -> str:
    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(status_code=401, detail="Sign in to view topics.")
    return str(owner)


def _shell(title: str, body: str) -> str:
    from pages.shell import page_shell

    return str(
        page_shell(
            title,
            body,
            active="topics",
            head_extra="""<link rel="stylesheet" href="/static/css/blog.css">
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
    /* long unbreakable titles (URLs/ids) must not push the action buttons
       offscreen — constrain the text column and keep buttons on-row */
    .paper-row>div:first-child{{min-width:0;flex:1 1 auto;}}
    .paper-row>div:last-child{{flex:0 0 auto;white-space:nowrap;align-self:center;}}
    .paper-row .pt{{overflow-wrap:anywhere;}}
    .paper-row .pt a{{color:var(--fg);font-weight:600;}}
    .paper-row .pm{{font-size:0.78rem;color:var(--fg-muted,#888);margin-top:0.2rem;}}
    .topic-actions a,.topic-actions button{{margin-right:0.5rem;}}
    .empty-note{{color:var(--fg-muted,#888);padding:2rem 0;text-align:center;}}
    .topic-section{{margin-top:1.5rem;}}
    .topic-section h3{{font-size:0.95rem;margin-bottom:0.5rem;}}
    .topic-section .blog-article{{max-height:480px;overflow-y:auto;
      border:1px solid #444;border-radius:8px;padding:1rem;}}
  </style>""",
            scripts="""<script src="/static/js/report.js?v=2" defer></script>
  <script src="/static/js/main.js?v=2" type="module"></script>
  <script src="/static/js/blog-view.js?v=1" defer></script>""",
            wrap_class="topics-wrap",
        )
    )


def _esc(v: object) -> str:
    return html.escape(str(v or ""))


def _share_url(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/share/{token}"


def _share_link_html(request: Request, topic: Topic) -> str:
    """The share-status content: the public URL + unshare control."""
    url = _share_url(request, topic.share_token or "")
    return (
        f'🔗 <a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a>'
        " — anyone with this link can view (read-only)."
        ' <button type="button" class="btn-secondary" style="font-size:0.7rem;padding:2px 8px;"'
        f' hx-post="/topics/{_esc(topic.id)}/unshare" hx-target="#share-status"'
        ' hx-swap="innerHTML">Unshare</button>'
    )


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


def _paper_rows_html(
    topic: Topic, papers: list[TopicPaper], *, readonly: bool = False
) -> str:
    """One rendered row per topic paper (shared by the page, feed refresh,
    and the public share page — readonly drops the remove/viewer buttons)."""
    paper_rows = []
    for p in papers:
        title_link = (
            f'<a href="{_esc(p.url)}" target="_blank" rel="noopener">{_esc(p.title)}</a>'
            if p.url
            else _esc(p.title)
        )
        feed_chip = (
            ' <span class="topic-kind" title="Added by the topic feed">feed</span>'
            if p.source == "feed"
            else ""
        )
        meta_bits = [b for b in (_fmt_authors(p.authors), _esc(p.published_date)) if b]
        meta = f"<div class='pm'>{' · '.join(meta_bits)}</div>" if meta_bits else ""
        actions = ""
        if not readonly:
            pid_q = quote(p.paper_id, safe="")
            viewer_btn = (
                f'<button type="button" class="copy-icon-btn" title="Ask Assistant about this paper"'
                f' hx-get="/viewer/{pid_q}?url={quote_plus(p.url)}"'
                f' hx-target="#modal-container" hx-swap="innerHTML">'
                f'<i class="fa-solid fa-comment-dots"></i></button>'
                if p.url
                else ""
            )
            actions = (
                f"<div>{viewer_btn}"
                f'<button type="button" class="copy-icon-btn" title="Remove from topic"'
                f' hx-post="/api/topics/{_esc(topic.id)}/papers/{quote(p.paper_id, safe="")}/remove"'
                f' hx-target="closest .paper-row" hx-swap="outerHTML">&times;</button></div>'
            )
        paper_rows.append(
            f"""<div class="paper-row">
  <div><div class="pt">{title_link}{feed_chip}</div>{meta}</div>
  {actions}
</div>"""
        )
    return "".join(paper_rows) or "<div class='empty-note'>No papers yet.</div>"


# Markdown → HTML, rendered server-side so a CDN hiccup can never surface raw
# markdown; JS only enhances (KaTeX) afterwards. LLM output can embed raw
# HTML — strip active-content tags and on* handlers before parsing.
_DANGEROUS = re.compile(
    r"(?is)</?\s*(script|iframe|object|embed|form|style|link|meta|base)[^>]*>"
)
_HANDLERS = re.compile(r"(?is)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_JS_SCHEME = re.compile(r"(?i)javascript\s*:")


def _md_to_html(md: str) -> str:
    safe = _JS_SCHEME.sub("", _HANDLERS.sub("", _DANGEROUS.sub("", md)))
    rendered: str = _markdown.markdown(
        safe, extensions=["extra", "sane_lists"], output_format="html"
    )
    return rendered


def _md_section(sec_id: str, heading: str, md: str | None, empty_msg: str) -> str:
    if not md:
        return (
            f'<div class="topic-section"><h3>{heading}</h3>'
            f'<div class="topic-meta">{empty_msg}</div></div>'
        )
    return (
        f'<div class="topic-section"><h3>{heading}</h3>'
        f'<div class="blog-scope">'
        f'<article id="{sec_id}" class="blog-article">{_md_to_html(md)}</article>'
        f"</div>"
        f'<script>document.addEventListener("DOMContentLoaded",function(){{'
        f"var r=window.ohBlogRender;r&&r.loadAllDeps().then(function(){{"
        f'r.renderKaTeX(document.getElementById("{sec_id}"));'
        f"}});}});</script></div>"
    )


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

    papers_html = _paper_rows_html(topic, papers)

    blog_html = _md_section(
        "topic-blog",
        "Blog",
        topic.blog_markdown,
        "No blog yet — generate one from a report.",
    )
    notes_html = _md_section(
        "topic-notes",
        "Notes",
        topic.notes_markdown,
        "No notes yet — generate study notes from a report.",
    )

    report_rows = []
    for r in reports:
        report_rows.append(
            f"<div class='topic-meta'>&#8226; <a href='/reports/{_esc(r.id)}'>"
            f"{_esc(_display_report_title(r))}</a> — {r.created_at.strftime('%b %d, %Y')}</div>"
        )
    reports_html = (
        "".join(report_rows) or "<div class='topic-meta'>No reports yet.</div>"
    )

    share_status_html = _share_link_html(request, topic) if topic.share_token else ""

    body = f"""<h1>{_esc(topic.title)} <span class="topic-kind">{_esc(topic.kind)}</span></h1>
<div class="topic-actions" style="margin:0.75rem 0;">
  <a class="btn-secondary" href="/" style="text-decoration:none;">+ New report</a>
  <button type="button" class="btn-secondary" title="Re-render the report against this topic's current papers"
    hx-post="/topics/{_esc(topic.id)}/report" hx-target="#topic-report-out"
    hx-swap="innerHTML" hx-disabled-elt="this">Refresh report</button>
  <button type="button" class="btn-secondary"
    hx-get="/chat-modal?paper_id=topic&topic_id={_esc(topic.id)}&title={quote('Chat about this topic')}"
    hx-target="#modal-container" hx-swap="innerHTML">Topic chat</button>
  <button type="button" class="btn-secondary" title="Get a public read-only link to this topic"
    hx-post="/topics/{_esc(topic.id)}/share" hx-target="#share-status"
    hx-swap="innerHTML">Share</button>
</div>
<div id="share-status" class="topic-meta">{share_status_html}</div>
<div id="topic-report-out"></div>
<div id="modal-container"></div>
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Reports</h2>
{reports_html}
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Papers ({len(papers)})
  <button type="button" class="btn-secondary" style="font-size:0.7rem;padding:2px 8px;"
    hx-post="/topics/{_esc(topic.id)}/feed" hx-target="#feed-status" hx-swap="innerHTML">Check for new</button>
</h2>
<div id="feed-status" class="topic-meta"></div>
<div id="topic-papers">{papers_html}</div>
{blog_html}
{notes_html}"""
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


@router.post("/topics/{topic_id}/feed", response_class=HTMLResponse)
async def topic_feed_now(topic_id: str, request: Request) -> HTMLResponse:
    """Manual feed check — polls this topic's source now and swaps in the
    updated paper list. Floored to one poll per minute so rapid clicks
    can't hammer providers; the automatic sweep handles regular polling."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from datetime import datetime

    from services.persistence.repositories import get_topic, list_topic_papers
    from services.topics import feed as topic_feed

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        if (
            topic.last_feed_check
            and (datetime.utcnow() - topic.last_feed_check).total_seconds()
            < topic_feed.MANUAL_MIN_INTERVAL_S
        ):
            return HTMLResponse(
                "Checked just now — the feed polls this topic automatically."
            )
        added = await topic_feed.feed_topic(session, topic)
        papers = await list_topic_papers(session, topic_id=topic.id)

    status = (
        f"<b>{added}</b> new paper{'s' if added != 1 else ''} added."
        if added
        else "Checked — nothing new yet."
    )
    oob = (
        f'<div id="topic-papers" hx-swap-oob="true">'
        f"{_paper_rows_html(topic, papers)}</div>"
    )
    return HTMLResponse(status + oob)


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


# ---------------------------------------------------------------------------
# Shareable topics — public read-only /share/{token} page
# ---------------------------------------------------------------------------


@router.post("/topics/{topic_id}/share", response_class=HTMLResponse)
async def topic_share(topic_id: str, request: Request) -> HTMLResponse:
    """Generate (or reuse) the topic's public share token; return the URL."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import get_topic, set_topic_share_token

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        if not topic.share_token:
            await set_topic_share_token(
                session,
                owner_id=owner,
                topic_id=topic_id,
                token=secrets.token_urlsafe(12),
            )
            await session.refresh(topic)
        return HTMLResponse(_share_link_html(request, topic))
    return HTMLResponse("")  # pragma: no cover - session_scope always yields


@router.post("/topics/{topic_id}/unshare", response_class=HTMLResponse)
async def topic_unshare(topic_id: str, request: Request) -> HTMLResponse:
    """Clear the share token — the public link goes dead immediately."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import get_topic, set_topic_share_token

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        await set_topic_share_token(
            session, owner_id=owner, topic_id=topic_id, token=None
        )
        return HTMLResponse("Share link disabled.")
    return HTMLResponse("")  # pragma: no cover


@router.get("/share/{token}", response_class=HTMLResponse)
async def share_page(token: str) -> HTMLResponse:
    """Public read-only view of a shared topic — no account needed."""
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import (
        get_topic_by_share_token,
        list_topic_papers,
    )

    async for session in session_scope():
        topic = await get_topic_by_share_token(session, token=token)
        if topic is None:
            raise HTTPException(status_code=404, detail="Shared topic not found")
        papers = await list_topic_papers(session, topic_id=topic.id)

    papers_html = _paper_rows_html(topic, papers, readonly=True)
    blog_html = _md_section("share-blog", "Blog", topic.blog_markdown, "No blog yet.")
    notes_html = _md_section(
        "share-notes", "Notes", topic.notes_markdown, "No notes yet."
    )
    body = f"""<h1>{_esc(topic.title)} <span class="topic-kind">{_esc(topic.kind)}</span></h1>
<div class="topic-meta">Shared workspace — read-only.
  <a href="/">Make your own with Office Hours →</a></div>
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Papers ({len(papers)})</h2>
<div id="topic-papers">{papers_html}</div>
{blog_html}
{notes_html}"""
    return HTMLResponse(_shell(topic.title, body))
