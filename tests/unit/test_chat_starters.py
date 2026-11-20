import re
from urllib.parse import quote_plus

import pytest

from services.chat.chat_session import COOKIE_NAME, serializer
from services.chat.chat_state import ChatMessage, get_chat_state


@pytest.mark.parametrize(
    ("path", "paper_id", "first_question"),
    [
        ("/chat-panel", "paper-1", "What is the main contribution of this paper?"),
        ("/chat-panel", "topic", "What are the main findings across these papers?"),
        ("/chat-modal", "paper-1", "What is the main contribution of this paper?"),
        (
            "/chat-modal",
            "topic:sample",
            "What are the main findings across these papers?",
        ),
        ("/chat-modal", "global", "How can I turn a topic into a research question?"),
    ],
)
def test_chat_starters_target_the_existing_chat_form(
    client, path, paper_id, first_question
):
    response = client.get(path, params={"paper_id": paper_id})
    assert response.status_code == 200

    form = re.search(
        r'<form\b(?=[^>]*\bhx-post="/chat")[^>]*\bid="([^"]+)"', response.text
    )
    assert form is not None
    buttons = re.findall(
        r'<button\b[^>]*class="(?:[^\"]*\s)?chat-starter(?:\s[^\"]*)?"[^>]*>',
        response.text,
    )
    assert len(buttons) == 3
    assert first_question in response.text
    assert all(
        f'form="{form[1]}"' in button and 'type="button"' in button
        for button in buttons
    )


@pytest.mark.parametrize("path", ["/chat-panel", "/chat-modal"])
def test_topic_threads_have_separate_transcript_keys(client, path):
    response = client.get(path, params={"paper_id": "topic", "report_id": "report-a"})
    assert response.status_code == 200
    session_id = serializer.loads(client.cookies[COOKIE_NAME])
    state = get_chat_state(session_id)
    state.append(
        "topic:report-a", ChatMessage(role="user", content="Previous question")
    )
    state.append(
        "topic:report-a",
        ChatMessage(role="assistant", content=r"Previous answer: \(x\)"),
    )

    reopened = client.get(path, params={"paper_id": "topic", "report_id": "report-a"})
    new_topic = client.get(path, params={"paper_id": "topic", "report_id": "report-b"})
    assert 'data-chat-key="topic:report-a"' in reopened.text
    assert 'data-chat-key="topic:report-b"' in new_topic.text
    assert "Previous question" in reopened.text
    assert r"Previous answer: \(x\)" in reopened.text
    assert "Previous question" not in new_topic.text


def test_viewer_passes_complete_paper_id_to_chat_panel(client):
    paper_id = "https://openreview.net/forum?id=paper&ref=reader"
    response = client.get(f"/viewer/{quote_plus(paper_id)}", params={"url": paper_id})
    assert response.status_code == 200
    assert f"/chat-panel?paper_id={quote_plus(paper_id)}" in response.text
