"""E2e: one query box routes to the right service.

The FSM contract — URL pins the paper, explicit author phrasing and
provider-confirmed bare names hit author search, everything else is a
topic search. Providers are stubbed at the boundary.
"""

from __future__ import annotations

import pytest

from services.paper.metadata.author_search import AuthorResult

_HX = {"HX-Request": "true"}


@pytest.mark.e2e
def test_url_query_pins_paper(client):
    resp = client.post(
        "/search",
        data={"search_query": "https://arxiv.org/abs/1706.03762"},
        headers=_HX,
    )
    assert resp.status_code == 200
    # URL route renders the pinned paper + the intent banner, not a topic.
    assert "paper link" in resp.text
    assert "Example Paper One" in resp.text


@pytest.mark.e2e
def test_explicit_author_phrasing_routes_to_author(client, monkeypatch):
    import services.paper.metadata.author_search as author_mod

    monkeypatch.setattr(
        author_mod,
        "search_authors",
        lambda *a, **k: [AuthorResult(author_id="a1", name="Vincent Sitzmann")],
    )
    resp = client.post(
        "/search",
        data={"search_query": "papers by Vincent Sitzmann"},
        headers=_HX,
    )
    assert resp.status_code == 200
    assert "intent-banner" in resp.text
    assert "Vincent Sitzmann" in resp.text
    # The topic fallback link stays reachable from the banner.
    assert "topic" in resp.text


@pytest.mark.e2e
def test_bare_name_with_match_routes_to_author(client, monkeypatch):
    import services.paper.metadata.author_search as author_mod

    monkeypatch.setattr(
        author_mod,
        "search_authors",
        lambda *a, **k: [AuthorResult(author_id="a2", name="Yann LeCun")],
    )
    resp = client.post(
        "/search",
        data={"search_query": "Yann LeCun"},
        headers=_HX,
    )
    assert resp.status_code == 200
    assert "Yann LeCun" in resp.text
    assert "intent-banner" in resp.text


@pytest.mark.e2e
def test_bare_name_without_match_falls_to_topic(client, monkeypatch):
    import services.paper.metadata.author_search as author_mod

    monkeypatch.setattr(author_mod, "search_authors", lambda *a, **k: [])
    resp = client.post(
        "/search",
        data={"search_query": "Neural Scaling Laws"},
        headers=_HX,
    )
    assert resp.status_code == 200
    # Topic results render — not an empty author page.
    assert "Example Paper One" in resp.text


@pytest.mark.e2e
def test_topic_query_routes_to_topic(client):
    resp = client.post(
        "/search",
        data={"search_query": "attention mechanisms in transformers"},
        headers=_HX,
    )
    assert resp.status_code == 200
    assert "Example Paper One" in resp.text


@pytest.mark.e2e
def test_author_provider_outage_degrades_to_topic(client, monkeypatch):
    import services.paper.metadata.author_search as author_mod
    from services.paper.metadata.author_search import S2UnavailableError

    def _boom(*a, **k):
        raise S2UnavailableError("rate limited")

    monkeypatch.setattr(author_mod, "search_authors", _boom)
    resp = client.post(
        "/search",
        data={"search_query": "papers by Vincent Sitzmann"},
        headers=_HX,
    )
    assert resp.status_code == 200
    # Explicit phrasing still shows the author surface with the
    # unavailable state — never a false "no such author" claim.
    assert "Vincent Sitzmann" in resp.text
