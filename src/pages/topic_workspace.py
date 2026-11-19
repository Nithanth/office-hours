"""Presentation for a topic's report, sources and exploration tools."""

import html
from typing import Any
from urllib.parse import quote, urlencode

from pages.shell import workspace_rail


def topic_workspace(
    *,
    topic: Any,
    siblings: list[Any],
    papers: list[Any],
    reports: list[Any],
    papers_html: str,
    reports_html: str,
    facets_html: str,
    related_html: str,
    notes_html: str,
    blog_html: str,
    share_html: str,
    notion_badge: str,
) -> str:
    esc = html.escape
    tid = esc(topic.id, quote=True)
    sources = []
    for index, paper in enumerate(papers[:8], 1):
        query = urlencode({"url": paper.url or "", "title": paper.title or "Paper"})
        sources.append(
            f'<button type="button" class="workspace-source" '
            f'hx-get="/viewer/{quote(paper.paper_id, safe="")}?{esc(query)}" '
            'hx-target="#modal-container" hx-swap="innerHTML">'
            f'<span>{index:02}</span><span>{esc(paper.title or "Untitled paper")}</span>'
            '<span aria-hidden="true">↗</span></button>'
        )
    report = (
        reports[0].report_html
        if reports
        else '<div class="workspace-empty"><h2>Your report starts here.</h2>'
        "<p>Explore the papers, then choose Update report to synthesize this topic. "
        "Each update keeps the previous version.</p></div>"
    )
    tabs = [
        ("report", "Report"),
        ("papers", f"Papers · {len(papers)}"),
        ("map", "Paper map"),
        ("notes", "Notes"),
        ("tracking", "Tracked searches"),
        ("blog", "Blog"),
    ]
    buttons = "".join(
        f'<button type="button" role="tab" id="tab-{key}" data-workspace-tab="{key}" '
        f'aria-selected="{str(key == "report").lower()}" aria-controls="workspace-{key}" '
        f'tabindex="{0 if key == "report" else -1}">{label}</button>'
        for key, label in tabs
    )
    return f"""<div class="marble-workspace topic-workspace">
      {workspace_rail([topic, *siblings], topic.id)}
      <section class="workspace-topic-sheet" data-topic-id="{tid}">
        <div class="workspace-breadcrumb"><a href="/topics">Workspace</a><span>/</span><span>Topic</span></div>
        <header class="workspace-topic-title"><h1>{esc(topic.title)}<span>_</span></h1>
        <p>{len(papers)} {"paper" if len(papers) == 1 else "papers"} · {len(reports)} report {"version" if len(reports) == 1 else "versions"} {notion_badge}</p></header>
        <div class="workspace-topic-actions"><button type="button" class="btn-secondary"
          hx-post="/topics/{tid}/report" hx-target="#topic-report-out" hx-swap="innerHTML" hx-disabled-elt="this">Update report</button>
          <button type="button" class="btn-secondary" hx-get="/chat-modal?paper_id=topic&amp;topic_id={tid}&amp;title=Chat%20about%20this%20topic"
            hx-target="#modal-container" hx-swap="innerHTML">Topic chat</button>
          <button type="button" class="btn-secondary" hx-post="/topics/{tid}/share" hx-target="#share-status" hx-swap="innerHTML">Share</button></div>
        <div id="share-status" class="topic-meta">{share_html}</div>
        <div id="topic-report-out" aria-live="polite"></div>
        <div class="workspace-tabs" role="tablist" aria-label="Topic views">{buttons}</div>
        <section id="workspace-report" role="tabpanel" aria-labelledby="tab-report" class="workspace-panel">
          <div class="workspace-report-grid"><article class="workspace-report">{report}</article>
            <aside class="workspace-source-margin"><h2>IN THIS TOPIC</h2>{''.join(sources) or '<p>No papers yet.</p>'}
              <details class="workspace-versions"><summary>Report versions</summary>{reports_html}</details></aside></div>
        </section>
        <section id="workspace-papers" role="tabpanel" aria-labelledby="tab-papers" class="workspace-panel" hidden>
          <div class="workspace-paper-heading"><h2>Papers (<span id="papers-count">{len(papers)}</span>)</h2>
          <button type="button" class="btn-secondary" hx-post="/topics/{tid}/feed" hx-target="#feed-status" hx-swap="innerHTML">Check for new papers</button></div>
          <label class="workspace-filter">Filter papers<input type="search" id="author-filter-input" placeholder="Title or author…"></label>
          <div id="feed-status" class="topic-meta" aria-live="polite"></div><div id="topic-papers">{papers_html}</div>
        </section>
        <section id="workspace-map" role="tabpanel" aria-labelledby="tab-map" class="workspace-panel" hidden>
          <div id="topic-paper-map" data-topic-id="{tid}"><p>Open Paper map to explore this topic.</p></div></section>
        <section id="workspace-notes" role="tabpanel" aria-labelledby="tab-notes" class="workspace-panel" hidden>{notes_html}</section>
        <section id="workspace-tracking" role="tabpanel" aria-labelledby="tab-tracking" class="workspace-panel" hidden>{facets_html}{related_html}</section>
        <section id="workspace-blog" role="tabpanel" aria-labelledby="tab-blog" class="workspace-panel" hidden>{blog_html}</section>
      </section></div>"""
