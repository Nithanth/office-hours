"""Topic pages and APIs — the topic is the unit; papers are its contents."""

from __future__ import annotations

import asyncio
import html
import json
import secrets
from typing import Any
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from loguru import logger

from api.routers.report_stream import _clean_generated_title
from services.core.model_prefs import prefs_owner_id
from services.persistence.database import database_available, session_scope
from services.persistence.models import Report, Topic, TopicPaper
from services.topics.keys import canonical_key_for, detect_topic_kind
from utils.date_utils import human_date


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
    from pages.shell import lib_subtabs_html, page_shell

    return str(
        page_shell(
            title,
            lib_subtabs_html("topics") + body,
            active="library",
            head_extra="""<link rel="stylesheet" href="/static/css/blog.css">
  <style>
    .topic-card{border:1px solid var(--container-border);border-radius:var(--oh-radius,8px);padding:1rem;margin:0.75rem 0;
      background:var(--container-bg);transition:border-color 0.15s;}
    .topic-card h2{font-size:1.05rem;margin:0 0 0.3rem;}
    .topic-card h2 a{text-decoration:none;color:var(--fg);}
    .topic-card h2 a:hover{text-decoration:underline;}
    .topic-meta{font-size:0.8rem;color:var(--fg-muted,#888);margin-top:0.25rem;}
    .topic-kind{margin-right:0.4rem;}
    .paper-row{display:flex;justify-content:space-between;gap:0.75rem;
      border:1px solid var(--container-border);border-radius:var(--oh-radius,8px);padding:0.75rem;margin:0.5rem 0;
      background:var(--container-bg);}
    /* long unbreakable titles (URLs/ids) must not push the action buttons
       offscreen — constrain the text column and keep buttons on-row */
    .paper-row>div:first-child{min-width:0;flex:1 1 auto;}
    .paper-row>div:last-child{flex:0 0 auto;white-space:nowrap;align-self:center;}
    .paper-row .pt{overflow-wrap:anywhere;}
    .paper-row .pt a{color:var(--fg);font-weight:600;}
    .paper-row .pm{font-size:0.78rem;color:var(--fg-muted,#888);margin-top:0.2rem;}
    .topic-actions a,.topic-actions button{margin-right:0.5rem;}
    .empty-note{color:var(--fg-muted,#888);padding:2rem 0;text-align:center;}
    .topic-section{margin-top:1.5rem;}
    .topic-section h3{font-size:0.95rem;margin-bottom:0.5rem;}
    .topic-section .blog-article{max-height:480px;overflow-y:auto;
      border:1px solid var(--container-border);border-radius:var(--oh-radius,8px);padding:1rem;}
  </style>""",
            scripts="""<script src="/static/js/report.js?v=2" defer></script>
  <script src="/static/js/main.js?v=2" type="module"></script>
  <script src="/static/js/blog-view.js?v=1" defer></script>""",
            wrap_class="page-wrap",
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
        f'Public link: <a href="{_esc(url)}" target="_blank" rel="noopener">{_esc(url)}</a>'
        ". Anyone with it can view the topic (read-only)."
        ' <button type="button" class="btn-secondary" style="font-size:0.7rem;padding:2px 8px;"'
        f' hx-post="/topics/{_esc(topic.id)}/unshare" hx-target="#share-status"'
        ' hx-swap="innerHTML">Unshare</button>'
    )


def _kind_label(kind: str | None) -> str:
    """Human chip for non-default kinds — 'subject' (the common case) gets
    no chip since a topic is a subject by definition."""
    return {"author": "Author", "paper": "Paper"}.get(kind or "", "")


def _fmt_authors(authors: object) -> str:
    """Byline text — strips Exa-scraped chrome ('View Profile', institutions)
    via the same cleaner the search cards use."""
    from pages.main_page import _clean_author_list

    if isinstance(authors, dict):
        authors = authors.get("items") or []
    if isinstance(authors, list):
        return ", ".join(_esc(a) for a in _clean_author_list(authors)[:4] if a)
    return _esc(authors)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/topics", response_class=HTMLResponse)
async def topics_page(request: Request) -> HTMLResponse:
    """Library landing — topics are the product unit, so this is home base."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.auth.session import get_auth_user_id
    from services.persistence.repositories import (
        feed_new_counts,
        get_notion_integration,
        home_stats,
        list_topics,
        topic_card_stats,
    )

    signed_in = bool(get_auth_user_id(request))
    notion_connected = False
    notion_needs_destination = False
    async for session in session_scope():
        topics = await list_topics(session, owner_id=owner)
        stats = await home_stats(session, owner_id=owner)
        new_counts = await feed_new_counts(session, topic_ids=[t.id for t in topics])
        card_stats = await topic_card_stats(session, owner_id=owner)
        if signed_in:
            integration = await get_notion_integration(
                session, user_id=str(get_auth_user_id(request))
            )
            if integration:
                notion_connected = bool(
                    integration.parent_page_id or integration.parent_database_id
                )
                notion_needs_destination = not notion_connected

    if notion_connected:
        sync_btn = (
            '<button id="lib-notion-sync-btn" class="btn-secondary oh-tip" type="button" '
            'data-tip="One shot: re-push every saved paper + topic to Notion and reply to '
            "unanswered comments on your synced pages. Sync already runs automatically "
            'when you save or change status. Use this only if Notion drifted">'
            "Sync now</button>"
        )
        notion_ui = ""
    elif notion_needs_destination:
        sync_btn = ""
        notion_ui = """<div class="lib-notion-card">
  <strong>Notion connected.</strong> One last step: pick where your library lives.
  <div class="lib-notion-pick">
    <select id="lib-notion-destination"><option value="">Loading your pages…</option></select>
    <button id="lib-notion-set-dest" type="button" class="btn-secondary">Set up sync</button>
  </div>
  <div class="lib-notion-hint">Office Hours creates two linked databases, Papers ↔ Topics, under the page you pick.</div>
</div>"""
    elif signed_in:
        sync_btn = ""
        notion_ui = """<div class="lib-notion-card lib-notion-banner">
  Mirror your library in Notion — papers, topics and reading status sync automatically.
  <a class="lib-notion-connect" href="/integrations/notion/connect?next=/topics">Connect Notion</a>
</div>"""
    else:
        sync_btn = ""
        notion_ui = ""

    # One muted line of context — stats + where to pick up, no card.
    context_line = ""
    if topics:
        bits = []
        if stats.get("topics"):
            bits.append(f"{stats['topics']} topics")
        if stats.get("reports"):
            bits.append(f"{stats['reports']} reports")
        if stats.get("saved"):
            bits.append(f"{stats['saved']} saved papers")
        top = topics[0]
        n = new_counts.get(top.id, 0)
        badge = f" (+{n} new)" if n else ""
        cont = (
            f' — pick up: <a href="/topics/{_esc(top.id)}">'
            f"{_esc(top.title)}</a>{badge}"
        )
        context_line = f'<div class="subtab-desc">{" · ".join(bits)}{cont}</div>'

    if not topics:
        items = (
            '<div class="empty-state-card"><strong>No topics yet.</strong>'
            "<p>Run a search and generate a report. Your topics land here.</p>"
            '<a href="/">New search</a></div>'
        )
    else:
        rows = []
        for t in topics:
            cs = card_stats.get(t.id, {})
            n = new_counts.get(t.id, 0)
            new_badge = f' <span class="oh-surface-badge">+{n} new</span>' if n else ""
            notion_badge = ""
            if t.notion_page_id:
                notion_url = "https://www.notion.so/" + t.notion_page_id.replace(
                    "-", ""
                )
                notion_badge = (
                    f' <a class="lib-notion-badge" href="{notion_url}" '
                    'target="_blank" title="Synced to Notion — open the page">'
                    "↗ Notion</a>"
                )
            report_bit = ""
            if cs.get("report_id"):
                report_bit = (
                    f' · <a href="/reports/{_esc(cs["report_id"])}">'
                    f"latest report</a>"
                )
            kind_label = _kind_label(t.kind)
            kind_chip = (
                f'<span class="topic-kind">{kind_label}</span>' if kind_label else ""
            )
            meta = (
                kind_chip + f"{cs.get('papers', 0)} papers{report_bit}"
                f" · updated {human_date(t.updated_at)}"
            )
            rows.append(
                f"""<div class="topic-card">
  <h2><a href="/topics/{_esc(t.id)}">{_esc(t.title)}</a>{new_badge}{notion_badge}</h2>
  <div class="topic-meta">{meta}</div>
</div>"""
            )
        items = "".join(rows)

    script = ""
    if notion_needs_destination or notion_connected:
        script = """<script>
  (function() {
    async function post(url, data) {
      const r = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams(data).toString(),
      });
      return r.json();
    }
    const destSel = document.getElementById("lib-notion-destination");
    if (destSel) {
      fetch("/api/notion/destinations").then(r => r.json()).then(res => {
        if (!res.ok) {
          const opt = document.createElement("option");
          opt.value = "";
          opt.textContent = res.detail || "Couldn't load pages";
          destSel.replaceChildren(opt);
          return;
        }
        const dests = (res.destinations || []).filter(d => d.type === "page" || d.type === "database");
        destSel.innerHTML = dests.length
          ? dests.map(d => `<option value="${d.id}" data-type="${d.type}">${d.type === "page" ? "📄" : "🗃"} ${d.title}</option>`).join("")
          : '<option value="">No shared pages. Re-connect and share a page</option>';
      }).catch(() => {
        destSel.innerHTML = '<option value="">Couldn\'t reach Notion — retry?</option>';
      });
    }
    const setDest = document.getElementById("lib-notion-set-dest");
    if (setDest) {
      setDest.addEventListener("click", function() {
        const opt = destSel.options[destSel.selectedIndex];
        if (!opt || !opt.value) return;
        setDest.disabled = true;
        post("/api/notion/destination", {
          destination_id: opt.value,
          destination_type: opt.dataset.type,
        }).then(res => {
          if (res.ok) {
            document.getElementById("lib-sync-status").textContent =
              "All set. Papers now sync to Notion automatically.";
            document.querySelector(".lib-notion-card").remove();
          } else {
            setDest.disabled = false;
          }
        });
      });
    }
    document.addEventListener("click", function(ev) {
      const sync = ev.target.closest("#lib-notion-sync-btn");
      if (sync) {
        sync.disabled = true;
        const statusEl = document.getElementById("lib-sync-status");
        statusEl.textContent = "Syncing to Notion…";
        fetch("/api/library/notion-sync", {method: "POST"})
          .then(r => r.json())
          .then(res => {
            sync.disabled = false;
            statusEl.textContent = res.ok
              ? `Synced: ${res.created} papers + ${res.topics_created} topics added, ${res.updated + res.topics_updated} updated.` +
                (res.status_pulled ? ` ${res.status_pulled} status pulled from Notion.` : "") +
                (res.comments_answered ? ` ${res.comments_answered} comment thread(s) answered.` : "")
              : (res.detail || `Sync failed (${res.failed || "?"} errors)`);
          });
      }
    });
  })();
</script>"""

    return HTMLResponse(
        _shell(
            "Topics",
            f"""<div class="lib-header"><h1>My topics</h1>{sync_btn}</div>
    {notion_ui}
    {context_line}
    <div id="lib-sync-status" style="font-size:0.8rem;color:var(--fg-muted,#888);margin-bottom:0.75rem;"></div>
    {items}
    {script}""",
        )
    )


def _status_script() -> str:
    from api.routes._status_widget import STATUS_CHANGE_SCRIPT

    return str(STATUS_CHANGE_SCRIPT)


def _paper_rows_html(
    topic: Topic,
    papers: list[TopicPaper],
    *,
    readonly: bool = False,
    statuses: dict[str, str] | None = None,
) -> str:
    """One rendered row per topic paper (shared by the page, feed refresh,
    and the public share page — readonly drops the remove/viewer buttons).

    ``statuses`` maps paper_id → saved reading status; marking one claims
    the paper into the library (the endpoint upserts)."""
    paper_rows = []
    for p in papers:
        title_link = (
            f'<a href="{_esc(p.url)}" target="_blank" rel="noopener">{_esc(p.title)}</a>'
            if p.url
            else _esc(p.title)
        )
        feed_chip = (
            ' <span class="topic-kind" title="Found automatically by a tracked search">auto-added</span>'
            if p.source == "feed"
            else ""
        )
        meta_bits = [
            b
            for b in (_fmt_authors(p.authors), _esc(human_date(p.published_date)))
            if b
        ]
        meta = f"<div class='pm'>{' · '.join(meta_bits)}</div>" if meta_bits else ""
        actions = ""
        if not readonly:
            from api.routes._status_widget import status_options

            pid_q = quote(p.paper_id, safe="")
            viewer_btn = (
                f'<button type="button" class="copy-icon-btn" title="Ask Assistant about this paper"'
                f' hx-get="/viewer/{pid_q}?url={quote_plus(p.url)}"'
                f' hx-target="#modal-container" hx-swap="innerHTML">'
                f'<i class="fa-solid fa-comment-dots"></i></button>'
                if p.url
                else ""
            )
            status_sel = status_options((statuses or {}).get(p.paper_id), p.paper_id)
            repro_btn = (
                f'<button type="button" class="copy-icon-btn" title="Can this be reproduced?"'
                f' hx-get="/repro-modal?topic_id={_esc(topic.id)}&paper_id={pid_q}"'
                f' hx-target="#modal-container" hx-swap="innerHTML">'
                f'<i class="fa-solid fa-flask"></i></button>'
            )
            actions = (
                f'<div style="display:flex;gap:6px;align-items:center;">{status_sel}{repro_btn}{viewer_btn}'
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


def _md_to_html(md: str) -> str:
    """Sanitized markdown -> HTML for topic blog/notes sections (shared util)."""
    from utils.md_render import render_markdown_safe

    return str(render_markdown_safe(md))


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


def _facets_section(topic: Topic, facets: list[Any]) -> str:
    """The topic's tracked queries — each one is polled by the feed."""
    items = []
    for f in facets:
        remove_btn = ""
        if len(facets) > 1:
            remove_btn = (
                f'<button type="button" class="facet-remove" title="Stop tracking" '
                f'hx-post="/topics/{_esc(topic.id)}/facets/{_esc(f.id)}/remove" '
                'hx-target="#facets-section" hx-swap="outerHTML">&times;</button>'
            )
        kind_label = _kind_label(f.kind)
        kind_chip = (
            f'<span class="facet-kind">{kind_label}</span> ' if kind_label else ""
        )
        items.append(
            f'<span class="oh-related-chip facet-chip">'
            f"{kind_chip}{_esc(f.query[:80])}{remove_btn}</span>"
        )
    return f"""<div id="facets-section">
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Tracked searches</h2>
<div class="topic-meta" style="margin-bottom:8px;">New papers matching any of these automatically join this topic's set.</div>
<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px;">
{''.join(items)}
</div>
<form hx-post="/topics/{_esc(topic.id)}/facets" hx-target="#facets-section" hx-swap="outerHTML"
      style="display:flex;gap:8px;align-items:center;">
  <input type="text" name="query" placeholder="Track another term, paper link, or author…"
         style="flex:1;min-width:200px;padding:5px 10px;font-size:0.85rem;border:1px solid var(--container-border,#ccc);border-radius:6px;background:var(--container-bg,#fff);color:var(--fg,#222);"/>
  <button type="submit" class="btn-secondary" style="font-size:0.8rem;">Add query</button>
</form>
<div id="facet-status" class="topic-meta"></div>
</div>"""


def _related_topics_section(topic: Topic, siblings: list[Topic]) -> str:
    """Adjacent topics the user also tracks — shared-word hint + one-click merge."""
    import re as _r

    def _sig_words(title: str) -> set[str]:
        return {
            w
            for w in _r.findall(r"[a-z0-9]+", title.lower())
            if len(w) > 3 and w not in _STOP_WORDS
        }

    mine = _sig_words(topic.title)
    related = [t for t in siblings if mine and len(mine & _sig_words(t.title)) >= 2][:3]
    if not related:
        return ""
    rows = []
    for t in related:
        rows.append(
            f"<div class='topic-meta' style='display:flex;gap:10px;align-items:center;'>"
            f"<a href='/topics/{_esc(t.id)}'>{_esc(t.title)}</a>"
            f"<button type='button' class='btn-secondary' style='font-size:0.7rem;padding:2px 8px;' "
            f"hx-post='/api/topics/{_esc(t.id)}/merge' hx-vals='{{\"into_id\": \"{_esc(topic.id)}\"}}' "
            f"hx-confirm='Merge &quot;{_esc(t.title)}&quot; into this topic? Its papers, tracked queries, and reports move here; the old topic is archived.' "
            f"hx-swap='none'>merge into this topic</button></div>"
        )
    return (
        "<h2 style='font-size:0.95rem;margin-top:1.5rem;'>Possibly related topics</h2>"
        + "".join(rows)
    )


_STOP_WORDS = {
    "with",
    "from",
    "using",
    "based",
    "learning",
    "neural",
    "networks",
    "deep",
    "paper",
    "papers",
    "research",
    "study",
    "analysis",
    "model",
    "models",
    "methods",
    "approach",
    "via",
    "towards",
    "about",
    "into",
}


@router.post("/topics/{topic_id}/facets", response_class=HTMLResponse)
async def add_facet(
    topic_id: str, request: Request, query: str = Form(...)
) -> HTMLResponse:
    """Add a tracked query to a topic and immediately harvest its papers."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import (
        add_topic_facet,
        get_topic,
        list_topic_facets,
    )
    from services.topics.keys import canonical_key_for, detect_topic_kind

    q = query.strip()[:300]
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")
    kind = await asyncio.to_thread(detect_topic_kind, q, confirm_bare_name=True)
    ckey = canonical_key_for(q, kind=kind)

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        _facet, created = await add_topic_facet(
            session, topic_id=topic.id, kind=kind, query=q, canonical_key=ckey
        )
        await session.commit()
        facets = await list_topic_facets(session, topic_id=topic.id)

    added = 0
    if created:
        try:
            from services.topics.feed import feed_facet_once

            added = await feed_facet_once(topic, kind=kind, query=q)
        except Exception as exc:  # noqa: BLE001 - harvest failure isn't fatal
            logger.warning("facet_initial_harvest_failed", err=str(exc))

    status = (
        f"<div class='topic-meta' style='margin-top:6px;'>"
        f"{'Added ' + str(added) + ' papers.' if created else 'Already tracking that query.'}</div>"
    )
    return HTMLResponse(_facets_section(topic, facets) + status)


@router.post("/topics/{topic_id}/facets/{facet_id}/remove", response_class=HTMLResponse)
async def remove_facet(topic_id: str, facet_id: str, request: Request) -> HTMLResponse:
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import (
        get_topic,
        list_topic_facets,
        remove_topic_facet,
    )

    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        removed = await remove_topic_facet(
            session, facet_id=facet_id, topic_id=topic.id
        )
        await session.commit()
        facets = await list_topic_facets(session, topic_id=topic.id)

    msg = (
        ""
        if removed
        else "<div class='topic-meta'>A topic needs at least one tracked query.</div>"
    )
    return HTMLResponse(_facets_section(topic, facets) + msg)


@router.post("/api/topics/{topic_id}/merge")
async def merge_topic_route(
    topic_id: str, request: Request, into_id: str = Form(...)
) -> JSONResponse:
    """Merge this topic into another — papers, facets, reports move; this one archives."""
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import merge_topics

    async for session in session_scope():
        target = await merge_topics(
            session, source_id=topic_id, into_id=into_id, owner_id=owner
        )
        await session.commit()
    if target is None:
        raise HTTPException(status_code=400, detail="Cannot merge these topics")
    return JSONResponse({"ok": True, "into_id": target.id})


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
        from services.persistence.repositories import (
            list_saved_statuses,
            list_topic_facets,
            list_topics,
        )

        facets = await list_topic_facets(session, topic_id=topic.id)
        statuses = await list_saved_statuses(
            session, owner_id=owner, paper_ids=[p.paper_id for p in papers]
        )
        siblings = [
            t
            for t in await list_topics(session, owner_id=owner)
            if t.id != topic.id and not t.archived
        ]

    facets_html = _facets_section(topic, facets)
    related_html = _related_topics_section(topic, siblings)

    papers_html = _paper_rows_html(topic, papers, statuses=statuses)

    blog_html = _md_section(
        "topic-blog",
        "Blog",
        topic.blog_markdown,
        "No blog yet — open a report version and use its Blog view.",
    )
    notes_html = _md_section(
        "topic-notes",
        "Notes",
        topic.notes_markdown,
        "No notes yet — open a report version and export notes; they land here.",
    )

    report_rows = []
    for r in reports:
        report_rows.append(
            f"<div class='topic-meta'>&#8226; <a href='/reports/{_esc(r.id)}'>"
            f"{_esc(_display_report_title(r))}</a> · {r.created_at.strftime('%b %d, %Y')}</div>"
        )
    reports_html = (
        "".join(report_rows) or "<div class='topic-meta'>No reports yet.</div>"
    )

    share_status_html = _share_link_html(request, topic) if topic.share_token else ""
    notion_badge = ""
    if topic.notion_page_id:
        notion_url = "https://www.notion.so/" + topic.notion_page_id.replace("-", "")
        notion_badge = (
            f' <a class="lib-notion-badge" href="{notion_url}" '
            'target="_blank" title="Synced to Notion — open the page">'
            "↗ Notion</a>"
        )

    topic_kind = _kind_label(topic.kind)
    topic_kind_chip = (
        f' <span class="topic-kind">{topic_kind}</span>' if topic_kind else ""
    )
    body = f"""<h1>{_esc(topic.title)}{topic_kind_chip}{notion_badge}</h1>
<div class="topic-actions" style="margin:0.75rem 0;">
  <button type="button" class="btn-secondary" title="Write a fresh report version from this topic's current papers"
    hx-post="/topics/{_esc(topic.id)}/report" hx-target="#topic-report-out"
    hx-swap="innerHTML" hx-disabled-elt="this">Update report</button>
  <button type="button" class="btn-secondary"
    hx-get="/chat-modal?paper_id=topic&topic_id={_esc(topic.id)}&title={quote('Chat about this topic')}"
    hx-target="#modal-container" hx-swap="innerHTML">Ask about this topic</button>
  <button type="button" class="btn-secondary" title="Get a public read-only link to this topic"
    hx-post="/topics/{_esc(topic.id)}/share" hx-target="#share-status"
    hx-swap="innerHTML">Share</button>
</div>
<div id="share-status" class="topic-meta">{share_status_html}</div>
<div id="topic-report-out"></div>
<div id="modal-container"></div>
<h2 style="font-size:0.95rem;margin-top:1rem;">Papers (<span id="papers-count">{len(papers)}</span>)
  <button type="button" class="btn-secondary" style="font-size:0.7rem;padding:2px 8px;"
    hx-post="/topics/{_esc(topic.id)}/feed" hx-target="#feed-status" hx-swap="innerHTML" title="Poll the tracked searches for newly published papers">Check for new papers</button>
</h2>
<div id="feed-status" class="topic-meta"></div>
<div id="topic-papers">{papers_html}</div>
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Report versions</h2>
<div class="topic-meta" style="margin-bottom:8px;">Each update snapshots the topic's papers at that moment. Older versions are kept.</div>
{reports_html}
{blog_html}
{notes_html}
{facets_html}
{related_html}
{_status_script()}"""
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

    kind = await asyncio.to_thread(detect_topic_kind, query, confirm_bare_name=True)
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
        if created:
            from services.persistence.repositories import ensure_seed_facet

            await ensure_seed_facet(session, topic=topic)
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
                "Checked just now. The feed polls this topic automatically."
            )
        added = await topic_feed.feed_topic(session, topic)
        papers = await list_topic_papers(session, topic_id=topic.id)
        from services.persistence.repositories import list_saved_statuses

        statuses = await list_saved_statuses(
            session, owner_id=owner, paper_ids=[p.paper_id for p in papers]
        )

    status = (
        f"<b>{added}</b> new paper{'s' if added != 1 else ''} added."
        if added
        else "Checked. Nothing new yet."
    )
    oob = (
        f'<div id="topic-papers" hx-swap-oob="true">'
        f"{_paper_rows_html(topic, papers, statuses=statuses)}</div>"
        f'<span id="papers-count" hx-swap-oob="true">{len(papers)}</span>'
    )
    return HTMLResponse(status + oob)


@router.post("/api/topics/{topic_id}/papers/{paper_id:path}/remove")
async def api_remove_paper(topic_id: str, paper_id: str, request: Request) -> Response:
    owner = _owner_id(request)
    if not database_available():
        raise HTTPException(status_code=503, detail="Database unavailable")
    from services.persistence.repositories import (
        get_topic,
        list_topic_papers,
        remove_topic_paper,
    )

    remaining = 0
    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found")
        removed = await remove_topic_paper(
            session, topic_id=topic_id, paper_id=paper_id
        )
        remaining = len(await list_topic_papers(session, topic_id=topic_id))
    if request.headers.get("hx-request"):
        # OOB-swap the Papers heading count so it doesn't go stale.
        return HTMLResponse(
            f'<span id="papers-count" hx-swap-oob="true">{remaining}</span>',
            status_code=200,
        )
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
    share_kind = _kind_label(topic.kind)
    share_kind_chip = (
        f' <span class="topic-kind">{share_kind}</span>' if share_kind else ""
    )
    body = f"""<h1>{_esc(topic.title)}{share_kind_chip}</h1>
<div class="topic-meta">Shared workspace. Read-only.
  <a href="/">Make your own with Office Hours →</a></div>
<h2 style="font-size:0.95rem;margin-top:1.5rem;">Papers ({len(papers)})</h2>
<div id="topic-papers">{papers_html}</div>
{blog_html}
{notes_html}"""
    return HTMLResponse(_shell(topic.title, body))
