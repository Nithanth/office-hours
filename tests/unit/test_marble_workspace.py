"""Real-data workspace, map ownership, and isolated subtopic retrieval."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from fastapi import HTTPException

from pages.topic_workspace import topic_workspace
from services.chat.topic_scope import scoped_topic_key, validate_topic_subset


async def test_reader_marks_are_private_to_owner_and_selected_document(monkeypatch):
    import re

    from api.routers import chat_modal as reader
    from services.core import model_prefs

    current_owner = ["alice"]
    monkeypatch.setattr(model_prefs, "prefs_owner_id", lambda _: current_owner[0])
    monkeypatch.setattr(
        reader,
        "_topic_papers",
        AsyncMock(
            return_value=[
                ("https://arxiv.org/abs/2005.11401", "First paper"),
                ("https://arxiv.org/pdf/1706.03762.pdf", "Second paper"),
            ]
        ),
    )

    async def keys():
        markup = await reader._topic_viewer_html(
            object(), report_id="r1", title=None, audience_type=None, model_key=None
        )
        return re.findall(r'data-reader-key="([^"]+)"', markup)

    alice = await keys()
    assert len(alice) == 3 and alice[0] == alice[1] and alice[1] != alice[2]
    assert alice[0] == reader._reader_storage_key(
        object(), "https://arxiv.org/pdf/2005.11401.pdf"
    )
    current_owner[0] = "bob"
    assert set(alice).isdisjoint(await keys())
    current_owner[0] = None
    assert (
        reader._reader_storage_key(object(), "https://arxiv.org/abs/2005.11401") == ""
    )


@pytest.fixture
def topic_data(monkeypatch):
    import services.core.model_prefs as prefs
    import services.persistence.database as db
    import services.persistence.repositories as repos

    async def sessions():
        yield object()

    topic = SimpleNamespace(id="t1", title="A <topic>", seed_query="seed")
    papers = [
        SimpleNamespace(
            paper_id="p1",
            title="Alpha",
            url="https://example.org/a",
            authors={"items": ["Ada"]},
            published_date="2024-01-01T00:00:00Z",
        ),
        SimpleNamespace(
            paper_id="p2",
            title="Beta",
            url="https://example.org/b",
            authors={"items": ["Bob"]},
            published_date=None,
        ),
    ]
    monkeypatch.setattr(prefs, "prefs_owner_id", lambda _: "owner")
    monkeypatch.setattr(db, "database_available", lambda: True)
    monkeypatch.setattr(db, "session_scope", sessions)
    monkeypatch.setattr(repos, "get_topic", AsyncMock(return_value=topic))
    monkeypatch.setattr(repos, "list_topic_papers", AsyncMock(return_value=papers))
    monkeypatch.setattr(
        repos, "list_saved_statuses", AsyncMock(return_value={"p1": "reading"})
    )
    monkeypatch.setattr(
        repos,
        "list_topic_reports",
        AsyncMock(return_value=[SimpleNamespace(report_text="FULL TOPIC SECRET")]),
    )
    return topic, papers, repos


async def test_map_only_reads_owned_topic(topic_data):
    from api.routes.cluster import _topic_paper_dicts

    _, _, repos = topic_data
    papers = await _topic_paper_dicts(object(), "t1")
    assert papers[0]["status"] == "reading"
    assert papers[0]["authors"] == ["Ada"]
    assert papers[0]["year"] == "2024"
    assert papers[1]["status"] == "to_read"
    repos.get_topic.assert_awaited_with(ANY, owner_id="owner", topic_id="t1")


async def test_map_rejects_another_owners_topic(topic_data):
    from api.routes.cluster import _topic_paper_dicts

    _, _, repos = topic_data
    repos.get_topic.return_value = None
    assert await _topic_paper_dicts(object(), "not-mine") is None
    repos.list_topic_papers.assert_not_awaited()


async def test_map_api_preserves_reading_status(client, monkeypatch):
    import sys
    import types

    from api.routes import cluster

    result = SimpleNamespace(
        papers=[{"id": "p1", "x": 0, "y": 0, "cluster": 0}],
        clusters=[{"id": 0, "label": "Retrieval"}],
    )
    module = types.ModuleType("services.paper.clustering")
    module.cluster_papers = lambda _: result
    monkeypatch.setitem(sys.modules, "services.paper.clustering", module)
    monkeypatch.setattr(cluster, "get_exa_client", lambda: object())
    monkeypatch.setattr(
        cluster,
        "_topic_paper_dicts",
        AsyncMock(
            return_value=[
                {"id": "p1", "status": "done", "url": "https://example.org/a"},
                {"id": "p2"},
            ]
        ),
    )
    response = client.post("/api/cluster?scope=topic&topic_id=t1")
    assert response.status_code == 200
    assert response.json()["papers"][0]["status"] == "done"
    assert response.json()["papers"][0]["url"] == "https://example.org/a"


@pytest.mark.parametrize("raw", ["[]", "not json", '["outside"]', "[1]", '[""]'])
async def test_subset_rejects_empty_invalid_or_foreign_papers(topic_data, raw):
    with pytest.raises(HTTPException) as error:
        await validate_topic_subset(object(), "t1", raw)
    assert error.value.status_code == 400


async def test_subset_rejects_foreign_topic(topic_data):
    _, _, repos = topic_data
    repos.get_topic.return_value = None
    with pytest.raises(HTTPException) as error:
        await validate_topic_subset(object(), "foreign", '["p1"]')
    assert error.value.status_code == 404


async def test_subset_has_stable_separate_history(topic_data):
    selected = await validate_topic_subset(object(), "t1", '["p2","p1","p1"]')
    assert selected == ["p1", "p2"]
    key = scoped_topic_key("topic:t1", selected)
    assert key == scoped_topic_key("topic:t1", ["p2", "p1"])
    assert key != scoped_topic_key("topic:t1", ["p1"])
    assert scoped_topic_key("topic:t1", None) == "topic:t1"


async def test_large_subtopic_preserves_all_owned_papers(topic_data, client):
    import json

    _, _, repos = topic_data
    ids = [f"https://example.org/paper/{i}" for i in range(250)]
    repos.list_topic_papers.return_value = [
        SimpleNamespace(paper_id=pid) for pid in ids
    ]
    selected = await validate_topic_subset(object(), "t1", json.dumps(ids))
    assert selected == sorted(ids)
    response = client.post(
        "/chat-modal?paper_id=topic&topic_id=t1",
        data={"paper_ids": json.dumps(ids)},
    )
    assert response.status_code == 200
    assert "name='paper_ids'" in response.text
    assert ids[-1] in response.text
    with pytest.raises(HTTPException):
        await validate_topic_subset(object(), "t1", json.dumps([*ids, "foreign"]))


async def test_subset_prompt_filters_owned_papers_and_omits_full_report(
    topic_data, monkeypatch
):
    from services.chat.chat_runtime import build_prompt
    from services.chat.chat_state import ChatMessage, get_chat_state
    from tests.unit.test_topic_prompt import _stub_rag

    captured = {}
    _stub_rag(
        monkeypatch,
        [{"text": "Alpha evidence", "section": "Intro", "paper_id": "p1"}],
        captured,
    )
    state = get_chat_state("subset-test")
    key = scoped_topic_key("topic:t1", ["p1"])
    state.paper_cache[key] = {"topic_paper_ids": ["p1"]}
    state.append(key, ChatMessage(role="user", content="What does this group show?"))
    bundle = await build_prompt(state, key, user_id="owner")
    assert captured["paper_ids"] == ["p1"]
    assert "Alpha evidence" in bundle.prompt
    assert "FULL TOPIC SECRET" not in bundle.prompt
    assert "restricted to the selected subtopic" in bundle.prompt


async def test_subset_with_missing_scope_does_not_search_every_paper(monkeypatch):
    from services.chat.chat_runtime import build_prompt
    from services.chat.chat_state import ChatMessage, get_chat_state
    from tests.unit.test_topic_prompt import _stub_rag

    captured = {}
    _stub_rag(monkeypatch, [], captured)
    state = get_chat_state("subset-missing")
    key = scoped_topic_key("topic:missing", ["p1"])
    state.append(key, ChatMessage(role="user", content="Compare"))
    bundle = await build_prompt(state, key)
    assert captured == {}
    assert bundle.sources == []


def test_workspace_keeps_real_topic_controls_and_escapes_titles(topic_data):
    topic, papers, _ = topic_data
    markup = topic_workspace(
        topic=topic,
        siblings=[],
        papers=papers,
        reports=[],
        report_count=7,
        papers_html="REAL PAPERS",
        reports_html="",
        facets_html="REAL QUERIES",
        related_html="",
        notes_html="REAL NOTES",
        blog_html="REAL BLOG",
        share_html="",
        notion_badge="",
    )
    assert "A &lt;topic&gt;" in markup
    assert "workspace-map" in markup and "workspace-report" in markup
    assert "REAL PAPERS" in markup and "REAL QUERIES" in markup
    assert "Your report starts here" in markup
    assert "7 report versions" in markup
    assert 'hx-post="/topics/t1/report"' in markup
    assert 'hx-get="/viewer/p1?' in markup


def test_home_loads_the_same_original_fonts_as_topic_shell(client):
    import html

    from pages.shell import FONT_STYLESHEET

    response = client.get("/")
    assert response.status_code == 200
    assert FONT_STYLESHEET in html.unescape(response.text)
    assert 'name="viewport"' in response.text
