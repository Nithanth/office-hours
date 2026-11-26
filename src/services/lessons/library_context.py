"""Resolve the current owner's saved source again at each lesson entry point."""

import contextlib
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup
from fastapi import HTTPException, Request

from services.core.model_prefs import prefs_owner_id
from services.lessons.planner import evidence
from services.persistence.database import database_available, session_scope
from services.persistence.repositories import (
    get_saved_paper,
    get_topic,
    list_topic_papers,
    list_topic_reports,
)


@dataclass(frozen=True)
class Paper:
    title: str
    url: str


@dataclass(frozen=True)
class LibraryContext:
    title: str
    papers: list[Paper]
    report: str = ""

    def revision(self) -> str:
        sources = [(paper.title, paper.url) for paper in self.papers]
        snapshot = json.dumps([self.title, sources, self.report]).encode()
        return hashlib.sha256(snapshot).hexdigest()


async def resolve_context(request: Request, paper_id: str, topic_id: str) -> LibraryContext | None:
    if not (paper_id or topic_id):
        return None
    if paper_id and topic_id:
        raise HTTPException(422, "Choose one saved paper or one topic.")
    if not database_available():
        raise HTTPException(503, "The library is unavailable.")
    owner = prefs_owner_id(request)
    if not owner:
        raise HTTPException(401, "Reload to start a session.")
    async for db in session_scope():
        if paper_id:
            saved = await get_saved_paper(db, owner_id=owner, paper_id=paper_id)
            if not saved:
                raise HTTPException(404, "Saved paper not found.")
            return LibraryContext(saved.title, [Paper(saved.title, saved.url or saved.paper_id)])
        topic = await get_topic(db, owner_id=owner, topic_id=topic_id)
        if not topic:
            raise HTTPException(404, "Topic not found.")
        reports = await list_topic_reports(db, topic_id=topic_id, limit=1)
        report = ""
        if reports:
            latest = reports[0]
            report = (
                latest.report_text
                or BeautifulSoup(latest.report_html or "", "html.parser").get_text(" ", strip=True)
            )[:10000]
        rows = await list_topic_papers(db, topic_id=topic_id)
        return LibraryContext(
            topic.title,
            [Paper(r.title, r.url or r.paper_id) for r in reversed(rows[-3:])],
            report,
        )
    raise HTTPException(503, "The library is unavailable.")


async def library_evidence(
    context: LibraryContext, first: int
) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    content: list[dict[str, Any]] = []
    refs: list[str] = []
    images: dict[str, str] = {}
    if context.report:
        refs.append("Topic report")
        content.append({"type": "text", "text": "Topic report\n" + context.report})
    for i, paper in enumerate(context.papers, 1):
        parts: list[dict[str, Any]] = []
        labels: list[str] = []
        if paper.url:
            with contextlib.suppress(HTTPException):
                parts, labels = await evidence(paper.url, b"", first)
        if not parts:
            continue
        prefix = f"{i}. {paper.title[:48]} · "
        refs.extend(prefix + label for label in labels)
        content.append(
            {
                "type": "text",
                "text": "Paper: "
                + paper.title
                + "\nSource labels: "
                + ", ".join(prefix + label for label in labels),
            }
        )
        pictures = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
        for label, picture in zip(labels, pictures, strict=False):
            images[prefix + label] = picture
        for part in parts:
            if part["type"] == "text" and "\n" in part["text"]:
                heading, body = part["text"].split("\n", 1)
                if heading in labels:
                    part = {"type": "text", "text": prefix + heading + "\n" + body}
            content.append(part)
    if not refs:
        if context.papers:
            raise HTTPException(
                422,
                "No readable source text was available. Check the paper link or choose a different starting page.",
            )
        refs.append("Saved topic overview")
        content.append({"type": "text", "text": "Topic title only: " + context.title})
    return content, refs, images
