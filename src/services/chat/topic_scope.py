"""Validate a map's paper subset and isolate its conversation from the topic."""

import hashlib
import json

from fastapi import HTTPException, Request


def scoped_topic_key(base_key: str, paper_ids: list[str] | None) -> str:
    if paper_ids is None:
        return base_key
    digest = hashlib.sha256(json.dumps(sorted(set(paper_ids))).encode()).hexdigest()[
        :16
    ]
    return f"{base_key}:subset:{digest}"


async def validate_topic_subset(
    request: Request,
    topic_id: str | None,
    raw: str | None,
) -> list[str] | None:
    if not isinstance(raw, str):
        return None
    if not topic_id or len(raw) > 20000:
        raise HTTPException(400, "Choose a topic and a valid paper selection.")
    try:
        paper_ids = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid paper selection.") from exc
    if (
        not isinstance(paper_ids, list)
        or not 1 <= len(paper_ids) <= 100
        or any(
            not isinstance(pid, str) or not pid or len(pid) > 2048 for pid in paper_ids
        )
    ):
        raise HTTPException(400, "Select between 1 and 100 papers.")

    from services.core.model_prefs import prefs_owner_id
    from services.persistence.database import database_available, session_scope
    from services.persistence.repositories import get_topic, list_topic_papers

    owner = prefs_owner_id(request)
    if not owner or not database_available():
        raise HTTPException(404, "Topic not found.")
    async for session in session_scope():
        topic = await get_topic(session, owner_id=owner, topic_id=topic_id)
        if topic is None:
            raise HTTPException(404, "Topic not found.")
        allowed = {
            p.paper_id for p in await list_topic_papers(session, topic_id=topic.id)
        }
        if not set(paper_ids).issubset(allowed):
            raise HTTPException(
                400, "The selected papers are no longer in this topic. Refresh the map."
            )
        return sorted(set(paper_ids))
    raise HTTPException(404, "Topic not found.")
