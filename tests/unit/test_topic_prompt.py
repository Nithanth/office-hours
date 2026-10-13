"""Topic-scope chat prompt: retrieval unions over a report's papers."""

from __future__ import annotations

import sys
import types

from services.chat.chat_runtime import PromptBundle, build_prompt
from services.chat.chat_state import ChatMessage, get_chat_state


def _stub_rag(monkeypatch, hits, captured):
    vs_stub = types.ModuleType("services.paper.vector_store")

    def _search_all(vec, *, query_text="", top_k=12, paper_ids=None):
        captured["paper_ids"] = paper_ids
        captured["query_text"] = query_text
        return hits or []

    vs_stub.search_all = _search_all
    vs_stub.index_exists = lambda pid: False  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "services.paper.vector_store", vs_stub)

    emb_stub = types.ModuleType("services.paper.embedding")
    emb_stub.embed_query = lambda q: [0.0]  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "services.paper.embedding", emb_stub)


async def test_topic_prompt_uses_search_all(monkeypatch) -> None:
    """paper_id 'topic:{rid}' retrieves via search_all across the paper set."""
    hits = [
        {"text": "Alpha on attention.", "section": "Intro", "paper_id": "p1"},
    ]
    captured: dict = {}
    _stub_rag(monkeypatch, hits, captured)

    state = get_chat_state("u-topic-1")
    state.append(
        "topic:r1", ChatMessage(role="user", content="what do the papers say?")
    )

    # user_id=None → no report lookup; paper set unknown → unfiltered union
    bundle = await build_prompt(state, "topic:r1", user_id=None)

    assert isinstance(bundle, PromptBundle)
    assert captured["paper_ids"] is None
    assert "research topic" in bundle.prompt
    assert "cite it inline as [n]" in bundle.prompt
    assert bundle.sources[0]["n"] == 1


async def test_topic_prompt_no_hits(monkeypatch) -> None:
    captured: dict = {}
    _stub_rag(monkeypatch, None, captured)

    state = get_chat_state("u-topic-2")
    state.append("topic:r9", ChatMessage(role="user", content="hi"))

    bundle = await build_prompt(state, "topic:r9")

    assert bundle.sources == []
    assert "cite it inline" not in bundle.prompt
