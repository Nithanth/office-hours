"""Topic feeds — poll external sources for new papers on a schedule.

Each topic kind maps to a provider path:
- ``subject`` → Exa publication search on the seed query, filtered to
  papers published since the last check.
- ``paper``   → Exa find-similar on the seed URL, same date filter.
- ``author``  → Semantic Scholar / OpenAlex author papers newer than the
  last check (re-resolves the author each sweep — cheap and survives
  provider id changes).

Papers join the topic with ``source='feed'``; the paper-set dedup lives in
``add_topic_papers`` so re-polling is naturally idempotent. The sweep loop
runs inside the app process (see ``feed_loop`` — started from lifespan),
one Railway container so no scheduler service is needed.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.paper import Paper
from services.persistence.models import Topic

# Tunables (env-overridable for ops without a deploy-time code change).
FEED_TOPIC_INTERVAL_S = int(os.getenv("FEED_TOPIC_INTERVAL_S", "21600"))  # 6h
FEED_SWEEP_S = int(os.getenv("FEED_SWEEP_S", "900"))  # sweep cadence
FEED_STARTUP_DELAY_S = int(os.getenv("FEED_STARTUP_DELAY_S", "30"))
FEED_LOOKBACK_DAYS = int(os.getenv("FEED_LOOKBACK_DAYS", "30"))
FEED_PER_TOPIC_LIMIT = int(os.getenv("FEED_PER_TOPIC_LIMIT", "8"))
# Floor between manual "check now" clicks and any provider call.
MANUAL_MIN_INTERVAL_S = int(os.getenv("FEED_MANUAL_MIN_INTERVAL_S", "60"))
# Cap on papers text-ingested per sweep — a feed add also makes the paper
# retrievable by topic chat, so we index it in the background.
FEED_INGEST_LIMIT = int(os.getenv("FEED_INGEST_LIMIT", "6"))


async def _ingest_feed_papers(papers: list[Paper]) -> None:
    """Index feed-added papers so topic chat retrieves them right away —
    unindexed papers are invisible to RAG until opened in the viewer.
    Fire-and-forget; every failure is a debug log."""
    try:
        from services.paper import vector_store as _vs
        from services.paper.text_extractor import TextExtractor
    except Exception as exc:  # noqa: BLE001
        logger.debug("feed_ingest_unavailable", err=str(exc))
        return

    ext = TextExtractor.instance()
    for paper in papers[:FEED_INGEST_LIMIT]:
        try:
            pid = str(paper.id) if paper.id else ""
            if not pid or _vs.index_exists(pid):
                continue
            await ext.ensure_cached(str(paper.url or paper.id), paper_id=pid)
            logger.debug("feed_ingested", paper_id=pid)
        except Exception as exc:  # noqa: BLE001
            logger.debug("feed_ingest_failed", paper_id=str(paper.id), err=str(exc))


def _exa_client() -> Any:
    from api.deps import get_exa_client

    return get_exa_client()


def _author_name(seed_query: str, title: str = "") -> str:
    from api.routes.search import _detect_author_query

    detected = _detect_author_query(seed_query or "")
    return str(detected) if detected else (seed_query or title)


def _author_paper_to_paper(ap: Any) -> Paper | None:
    from services.paper.metadata.author_search import get_paper_url

    url = get_paper_url(ap)
    if not url:
        return None  # Paper.url is required — unresolvable papers get dropped
    return Paper(
        id=url,
        title=ap.title or "Untitled",
        url=url,
        authors=[],
        published_date=str(ap.year) if ap.year else None,
        relevance_score=None,
        source="author_feed",
    )


def _fetch_candidates_sync(
    kind: str, query: str, *, since: datetime, log_ctx: str = ""
) -> list[Paper]:
    """Blocking provider fetch for one angle — run via ``asyncio.to_thread``."""
    papers: list[Paper] = []
    if kind == "author":
        from services.paper.metadata.author_search import (
            S2UnavailableError,
            get_author_papers,
            search_authors,
        )

        name = _author_name(query)
        try:
            authors = search_authors(name, limit=1, timeout=10)
            if not authors:
                return []
            s2_papers = get_author_papers(
                authors[0].author_id, limit=FEED_PER_TOPIC_LIMIT * 3
            )
        except S2UnavailableError:
            logger.warning("feed_author_unavailable", ctx=log_ctx)
            return []
        for ap in s2_papers:
            if ap.year and ap.year < since.year:
                continue
            paper = _author_paper_to_paper(ap)
            if paper is not None:
                papers.append(paper)
            if len(papers) >= FEED_PER_TOPIC_LIMIT:
                break
        return papers

    client = _exa_client()
    if client is None:
        logger.warning("feed_no_exa_client", ctx=log_ctx)
        return []
    if kind == "paper":
        papers = client.find_similar_papers(
            query,
            num_results=FEED_PER_TOPIC_LIMIT,
            start_published_date=since,
        )
    else:  # subject
        papers = client._fetch_papers_from_exa(  # noqa: SLF001
            query,
            FEED_PER_TOPIC_LIMIT,
            category="publication",
            start_published_date=since,
        )
    return papers[:FEED_PER_TOPIC_LIMIT]


async def fetch_feed_papers(topic: Topic, *, since: datetime) -> list[Paper]:
    """Provider-facing fetch for a topic — every facet contributes candidates.

    A topic is a research question; its facets are the different vocabularies
    into it. Each facet polls by its own kind (subject/paper/author) and the
    results merge into the shared paper set.
    """
    from services.persistence.repositories import list_topic_facets

    facets: list[tuple[str, str]] = []
    try:
        from services.persistence.database import session_scope

        async for s in session_scope():
            facets = [
                (f.kind, f.query or "")
                for f in await list_topic_facets(s, topic_id=topic.id)
            ]
    except Exception as exc:  # noqa: BLE001 - facets table may not exist yet
        logger.debug("feed_facets_fallback", err=str(exc))
    if not facets:
        facets = [(topic.kind, topic.seed_query or topic.title)]

    papers: list[Paper] = []
    for kind, query in facets:
        if not query:
            continue
        try:
            papers.extend(
                await asyncio.to_thread(
                    _fetch_candidates_sync,
                    kind,
                    query,
                    since=since,
                    log_ctx=f"{topic.id}:{kind}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad facet can't kill a sweep
            logger.warning(
                "feed_fetch_failed", topic_id=topic.id, kind=kind, err=str(exc)
            )
    return papers


async def feed_topic(
    session: AsyncSession, topic: Topic, *, now: datetime | None = None
) -> int:
    """Poll all of a topic's facets and add any new papers. Returns count added."""
    from services.persistence.repositories import add_topic_papers

    now = now or datetime.utcnow()
    since = topic.last_feed_check or (now - timedelta(days=FEED_LOOKBACK_DAYS))
    papers = await fetch_feed_papers(topic, since=since)
    added = await add_topic_papers(
        session, topic_id=topic.id, papers=papers, source="feed"
    )
    topic.last_feed_check = now
    if added:
        topic.updated_at = now
        asyncio.create_task(_ingest_feed_papers(papers))
    await session.commit()
    return int(added)


async def feed_facet_once(topic: Topic, *, kind: str, query: str) -> int:
    """One-shot harvest for a freshly added facet — pulls its current papers
    in immediately rather than waiting for the next sweep."""
    papers = await asyncio.to_thread(
        _fetch_candidates_sync,
        kind,
        query,
        since=datetime.utcnow() - timedelta(days=FEED_LOOKBACK_DAYS),
        log_ctx=f"{topic.id}:{kind}:init",
    )
    if not papers:
        return 0
    from services.persistence.database import session_scope
    from services.persistence.repositories import add_topic_papers

    async for session in session_scope():
        added = await add_topic_papers(
            session, topic_id=topic.id, papers=papers, source="feed"
        )
        await session.commit()
        if added:
            asyncio.create_task(_ingest_feed_papers(papers))
        return int(added)
    return 0


async def feed_sweep() -> int:
    """One pass over all due topics. Returns the number actually checked."""
    from services.persistence.database import database_available, session_scope

    if not database_available():
        return 0
    cutoff = datetime.utcnow() - timedelta(seconds=FEED_TOPIC_INTERVAL_S)
    async for session in session_scope():
        result = await session.execute(
            select(Topic.id).where(
                Topic.archived.is_(False),
                or_(
                    Topic.last_feed_check.is_(None),
                    Topic.last_feed_check < cutoff,
                ),
            )
        )
        due_ids = list(result.scalars().all())

    checked = 0
    for tid in due_ids:
        try:
            async for session in session_scope():
                topic = await session.get(Topic, tid)
                if topic is None:
                    continue
                added = await feed_topic(session, topic)
                checked += 1
                if added:
                    logger.info("feed_new_papers", topic_id=tid, added=added)
        except Exception as exc:  # noqa: BLE001 - one bad topic can't stop the sweep
            logger.warning("feed_topic_failed", topic_id=tid, err=str(exc))
        # Gentle on the S2 1 req/s budget and Exa's shared quota.
        await asyncio.sleep(1.5)
    return checked


async def feed_loop() -> None:
    """Background sweep — started from the app lifespan."""
    await asyncio.sleep(FEED_STARTUP_DELAY_S)
    while True:
        try:
            await feed_sweep()
        except Exception as exc:  # noqa: BLE001
            logger.warning("feed_sweep_failed", err=str(exc))
        await asyncio.sleep(FEED_SWEEP_S)
