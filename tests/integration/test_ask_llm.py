"""Integration tests for the `/ask-llm` endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_ask_llm_happy_path(client):
    resp = client.post(
        "/ask-llm",
        data={
            "question": "Explain RLHF in one paragraph",
            "audience": "technical",
        },
    )
    assert resp.status_code == 200
    # Stubbed ReportGenerator returns simple HTML answer
    assert "<p>ans</p>" in resp.text


@pytest.mark.integration
def test_ask_llm_unknown_model_key(client):
    resp = client.post(
        "/ask-llm",
        data={
            "question": "What is Transformers?",
            "audience": "technical",
            "model_key": "unknown_model",
        },
    )
    assert resp.status_code == 200
    assert "<p>ans</p>" in resp.text


@pytest.mark.integration
def test_ask_llm_llm_unavailable(client, monkeypatch):
    # Simulate LLM service down
    import main as app_module  # local import to access singleton

    monkeypatch.setattr(
        app_module.llm_service_instance, "_initialized", False, raising=False
    )

    resp = client.post(
        "/ask-llm",
        data={"question": "Hi", "audience": "technical"},
    )
    assert resp.status_code == 503
