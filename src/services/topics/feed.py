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


def _exa_client() -> Any:
    from api.deps import get_exa_client

    return get_exa_client()


def _author_name(topic: Topic) -> str:
    from api.routes.search import _detect_author_query

    detected = _detect_author_query(topic.seed_query or "")
    return str(detected) if detected else (topic.seed_query or topic.title)


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


def _fetch_candidates_sync(topic: Topic, *, since: datetime) -> list[Paper]:
    """Blocking provider fetch — run via ``asyncio.to_thread``."""
    papers: list[Paper] = []
    if topic.kind == "author":
        from services.paper.metadata.author_search import (
            S2UnavailableError,
            get_author_papers,
            search_authors,
        )

        name = _author_name(topic)
        try:
            authors = search_authors(name, limit=1, timeout=10)
            if not authors:
                return []
            s2_papers = get_author_papers(
                authors[0].author_id, limit=FEED_PER_TOPIC_LIMIT * 3
            )
        except S2UnavailableError:
            logger.warning("feed_author_unavailable", topic_id=topic.id)
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
        logger.warning("feed_no_exa_client", topic_id=topic.id)
        return []
    if topic.kind == "paper":
        papers = client.find_similar_papers(
            topic.seed_query,
            num_results=FEED_PER_TOPIC_LIMIT,
            start_published_date=since,
        )
    else:  # subject
        papers = client._fetch_papers_from_exa(  # noqa: SLF001
            topic.seed_query or topic.title,
            FEED_PER_TOPIC_LIMIT,
            category="publication",
            start_published_date=since,
        )
    return papers[:FEED_PER_TOPIC_LIMIT]


async def fetch_feed_papers(topic: Topic, *, since: datetime) -> list[Paper]:
    """Provider-facing fetch for one topic (off-thread; providers are sync)."""
    try:
        return await asyncio.to_thread(_fetch_candidates_sync, topic, since=since)
    except Exception as exc:  # noqa: BLE001 - a flaky provider must not kill a sweep
        logger.warning("feed_fetch_failed", topic_id=topic.id, err=str(exc))
        return []


async def feed_topic(
    session: AsyncSession, topic: Topic, *, now: datetime | None = None
) -> int:
    """Poll one topic's source and add any new papers. Returns count added."""
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
    await session.commit()
    return int(added)


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
