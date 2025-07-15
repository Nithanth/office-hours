"""Integration tests for search & shuffle endpoints."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_search_paper_flow(client):
    resp = client.post(
        "/search",
        data={"search_query": "transformer models", "num_results": 3},
        headers={"HX-Request": "true"},  # Simulate HTMX form submission
    )
    assert resp.status_code == 200
    # Should contain at least one <li> from rendered results
    assert "<li" in resp.text
    # Our stub returns Example Paper One title
    assert "Example Paper One" in resp.text


@pytest.mark.integration
def test_search_invalid_date(client):
    resp = client.post(
        "/search",
        data={
            "search_query": "transformer",
            "start_published_date": "invalid-date",
        },
        headers={"HX-Request": "true"},
    )
    # Service should return 200 with error banner (rather than 500)
    assert resp.status_code == 200
    assert "Invalid date format" in resp.text


@pytest.mark.integration
def test_search_direct_query(client):
    resp = client.post(
        "/search",
        data={"search_query": "What is RLHF?", "mode": "direct"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    # Stubbed generate_direct_query_answer returns "<p>ans</p>"
    assert "<p>ans</p>" in resp.text


@pytest.mark.integration
def test_shuffle_and_shuffle_all(client):
    # 1. Shuffle wildcard (replace_wildcard)
    resp = client.post("/shuffle")
    assert resp.status_code == 200
    assert "<li" in resp.text
    # Should include wildcard item id
    assert "wild-card-item" in resp.text

    # 2. Shuffle all
    resp_all = client.post("/shuffle_all")
    assert resp_all.status_code == 200
    # Should contain multiple list items
    assert resp_all.text.count("<li") >= 1
