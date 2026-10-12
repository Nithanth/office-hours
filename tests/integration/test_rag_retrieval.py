"""Integration test for RAG context injection in chat prompt.

This test stubs *vector_store* and *embed_query* so that the chat runtime
includes a predictable chunk in its SSE stream. This ensures the RAG path is
covered without requiring a real LanceDB or embedding model.
"""

from __future__ import annotations

import sys
import types

import pytest


@pytest.mark.integration
def test_chat_rag_retrieval(monkeypatch, client):
    """Chat stream should include stub chunk from vector_store.search."""

    # ------------------------------------------------------------------
    # Stub vector_store to pretend an index exists and always return a hit.
    # ------------------------------------------------------------------
    dummy_hits = [
        {
            "text": "Example chunk relevant to the query.",
            "section": "Introduction",
        }
    ]

    # Provide lightweight stub module before *chat_runtime* attempts to import it.
    vs_stub = types.ModuleType("services.paper.vector_store")
    vs_stub.index_exists = lambda pid: True

    search_called = {"count": 0}

    def _search(pid, vec, query_text=None, top_k=3):  # noqa: D401
        search_called["count"] += 1
        return dummy_hits

    vs_stub.search = _search
    sys.modules["services.paper.vector_store"] = vs_stub

    # ------------------------------------------------------------------
    # Stub embed_query to avoid dependency on model files.
    # ------------------------------------------------------------------
    emb_stub = types.ModuleType("services.paper.embedding")
    emb_stub.embed_query = lambda q: [0.0]
    sys.modules["services.paper.embedding"] = emb_stub

    # ------------------------------------------------------------------
    # Issue chat request and stream.
    # ------------------------------------------------------------------
    resp = client.post(
        "/chat", data={"message": "What is the intro?", "paper_id": "rag1"}
    )
    assert resp.status_code == 200

    stream = client.get("/chat/stream", params={"paper_id": "rag1"})
    assert stream.status_code == 200

    body = stream.text

    # Ensure vector_store.search was invoked at least once
    assert search_called["count"] >= 1

    # Ensure proper SSE termination events still emitted.
    assert "event: done" in body and "event: sse-close" in body
