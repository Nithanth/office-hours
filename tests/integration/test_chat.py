"""Integration tests for chat endpoints (HTML + SSE).

These tests rely on fixtures from ``conftest.py`` which stub heavy
LLM interactions so they run fast and deterministically.
"""

from __future__ import annotations

import re

import pytest


@pytest.mark.integration
def test_chat_post_and_stream(client):
    # 1. Send a chat message
    resp = client.post("/chat", data={"message": "Hello", "paper_id": "test-paper"})
    assert resp.status_code == 200

    # The response should contain a container div with hx-sse connect attr
    assert "assistant-response-" in resp.text
    assert 'sse-connect="/chat/stream?paper_id=test-paper"' in resp.text

    # 2. Open the SSE stream – TestClient fetches full buffered body.
    resp_stream = client.get("/chat/stream", params={"paper_id": "test-paper"})
    assert resp_stream.status_code == 200

    body = resp_stream.text
    # Should contain at least one data: line with our stub token
    assert re.search(r"^data: stub", body, re.MULTILINE)
    # Should finish with done + sse-close events
    assert "event: done" in body
    assert "event: sse-close" in body
