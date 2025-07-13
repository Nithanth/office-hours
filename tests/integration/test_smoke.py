# mypy: ignore-errors
from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Ensure `src` is on sys.path so that `import main` resolves to src/main.py even when
# PYTHONPATH is not set (e.g., CI test runners).
project_root = pathlib.Path(__file__).resolve().parents[2]
src_path = project_root / "src"
if src_path.as_posix() not in sys.path:
    sys.path.insert(0, src_path.as_posix())

# ruff: noqa: E402 - intentional import order (sys.path setup before importing app)
import main as app_module  # The main FastAPI application lives here
from api.routes import ask_llm, report, search


class _DummyService:  # very lightweight stub for external services
    """Trivial stub object that pretends to be initialised and async-compatible."""

    _initialized: bool = True

    async def __aenter__(self, *args: Any, **kwargs: Any):  # noqa: D401 - contextmanager helpers
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any):
        return False

    # Generic async placeholder used by LLMService methods in the codebase
    async def generate_completion_stream(self, *args: Any, **kwargs: Any):  # noqa: D401
        async def _iter():
            yield "stub"

        return _iter()


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch):
    """Replace heavyweight service singletons with inert stubs for ALL tests."""

    dummy = _DummyService()

    # Patch global singletons on the *app* module
    monkeypatch.setattr(app_module, "llm_service_instance", dummy, raising=False)
    monkeypatch.setattr(app_module, "exa_client_instance", dummy, raising=False)
    monkeypatch.setattr(app_module, "paper_service_instance", dummy, raising=False)

    # Patch already-imported route modules that cache these globals at import time
    monkeypatch.setattr(ask_llm, "llm_service_instance", dummy, raising=False)
    monkeypatch.setattr(ask_llm, "exa_client_instance", dummy, raising=False)
    monkeypatch.setattr(search, "llm_service_instance", dummy, raising=False)
    monkeypatch.setattr(search, "exa_client_instance", dummy, raising=False)
    monkeypatch.setattr(report, "llm_service_instance", dummy, raising=False)
    monkeypatch.setattr(report, "exa_client_instance", dummy, raising=False)

    # Patch TextExtractor singleton access used in _prewarm_cached
    import importlib

    _te_mod = importlib.import_module("infra.text.text_extractor")
    monkeypatch.setattr(_te_mod.TextExtractor, "instance", lambda *a, **k: dummy, raising=False)

    # Stub out heavyweight ReportGenerator methods so they return immediately
    from services import report_generator as _rg

    async def _fake_answer(*args: Any, **kwargs: Any) -> str:  # noqa: D401
        return "<p>stub answer</p>"

    async def _fake_report(*args: Any, **kwargs: Any) -> str:  # noqa: D401
        return "<p>stub report</p>"

    monkeypatch.setattr(
        _rg.ReportGenerator, "generate_direct_query_answer", _fake_answer, raising=False
    )
    monkeypatch.setattr(_rg.ReportGenerator, "generate_report_html", _fake_report, raising=False)

    # Stub out UI error rendering to avoid fasthtml dependency during tests
    from types import SimpleNamespace

    from pages import main_page as _mp

    _fake_div = SimpleNamespace(
        render=lambda: "<div>stub error</div>"
    )  # minimal object with .render()
    monkeypatch.setattr(_mp, "render_error_message", lambda *a, **k: _fake_div, raising=False)
    # Ensure the route module references the same stub (it imported the function by name)
    monkeypatch.setattr(report, "render_error_message", lambda *a, **k: _fake_div, raising=False)

    # No return - autouse fixture


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Shared TestClient instance for the whole test session."""

    return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# Smoke tests - ensure endpoints respond (status 200-399) with dummy services.
# ---------------------------------------------------------------------------


def _assert_ok(resp):
    assert resp.status_code < 400, resp.text[:200]


def _assert_no_server_error(resp):
    """Ensure request didn't error on the server (5xx). Accepts 4xx validation."""
    assert resp.status_code < 500, resp.text[:200]


def test_home_page(client: TestClient):
    _assert_ok(client.get("/"))


def test_search_endpoint(client: TestClient):
    resp = client.post(
        "/search",
        data={"search_query": "test query"},
        headers={"HX-Request": "true"},  # mimics HTMX form
    )
    _assert_ok(resp)


def test_ask_llm_endpoint(client: TestClient):
    resp = client.post(
        "/ask-llm",
        data={"question": "What is a photon?"},
        headers={"HX-Request": "true"},
    )
    _assert_ok(resp)


def test_generate_report_endpoint(client: TestClient):
    # minimal payload - invalid paper JSON is fine; route returns graceful error banner
    resp = client.post(
        "/generate-report",
        data={
            "original_query_for_report": "quantum computing",
            "selected_papers_json": '{"id":"p1","title":"Dummy Paper","url":"https://example.com"}',
            "audience_type": "technical",
            "summary_size": "M",
            "selected_model_key": "gemini_2_5_pro_preview",
            "is_url_input": "false",
        },
        headers={"HX-Request": "true"},
    )
    _assert_no_server_error(resp)
