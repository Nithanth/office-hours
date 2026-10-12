"""Advanced chat flow tests: abort endpoint and resume logic."""

from __future__ import annotations

import re

import pytest

from services.chat.chat_state import get_chat_state


@pytest.mark.integration
def test_chat_abort_endpoint(client):
    # Start a session by hitting /chat
    resp = client.post("/chat", data={"message": "Hi", "paper_id": "p1"})
    assert resp.status_code == 200

    # Extract session cookie
    luxsid = resp.cookies.get("luxsid")
    assert luxsid

    # Manually mark stream_active to simulate long-running SSE
    cs = get_chat_state(luxsid)
    cs.stream_active = True

    # Call abort
    resp_abort = client.post(
        "/chat/abort", data={"paper_id": "p1"}, cookies={"luxsid": luxsid}
    )
    assert resp_abort.status_code == 204
    assert cs.stream_active is False


@pytest.mark.integration
def test_chat_stream_resume(client):
    # 1. Post message to create state
    resp1 = client.post("/chat", data={"message": "Hello", "paper_id": "p1"})
    luxsid = resp1.cookies.get("luxsid")

    # 2. First stream to fill buffer
    resp_stream = client.get(
        "/chat/stream", params={"paper_id": "p1"}, cookies={"luxsid": luxsid}
    )
    assert resp_stream.status_code == 200
    body1 = resp_stream.text
    # Expect three stub tokens (may be batched into one or more SSE events)
    assert body1.count("stub") >= 3

    # 3. Resume at token 2 (should replay last part)
    resp_resume = client.get(
        "/chat/stream",
        params={"paper_id": "p1", "resume_at": 2},
        cookies={"luxsid": luxsid},
    )
    assert resp_resume.status_code == 200
    body2 = resp_resume.text
    # Should still contain at least one data line
    assert re.search(r"^data: stub", body2, re.MULTILINE)
    # Ensure done event present
    assert "event: done" in body2
