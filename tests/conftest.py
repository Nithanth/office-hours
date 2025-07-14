"""Shared pytest fixtures for the Lux Scholar test-suite.

This file is imported automatically by *pytest* so the fixtures are
visible to **all** tests (unit + integration).
"""

from __future__ import annotations

import contextlib
import importlib
import importlib as _imp  # stdlib alias to avoid shadowing real importlib use
import pathlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from models.paper import Paper

# Plugin auto-registration must run *after* imports to avoid E402.
pytest_plugins: list[str] = []
if _imp.util.find_spec("pytest_asyncio") is not None:
    pytest_plugins.append("pytest_asyncio")


# ---------------------------------------------------------------------------
# Ensure `src/` is on sys.path so `import main` resolves during tests.
# ---------------------------------------------------------------------------
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if SRC_DIR.as_posix() not in sys.path:
    sys.path.insert(0, SRC_DIR.as_posix())

# Import the FastAPI bootstrap *after* path tweak
import main as app_module  # noqa: E402, F401


class _DummyService:  # noqa: D101 – testing stub
    _initialized = True

    async def __aenter__(self, *a: Any, **kw: Any):  # noqa: D401 – context manager helper
        return self

    async def __aexit__(self, *a: Any, **kw: Any):
        return False

    # Generic async placeholder used by LLMService methods
    async def generate_completion_stream(self, *args: Any, **kwargs: Any):  # noqa: D401
        async def _iter():
            for _ in range(3):
                yield "stub"

        return _iter()

    async def generate_completion(self, *args: Any, **kwargs: Any):  # noqa: D401
        return "stub"


@pytest.fixture(autouse=True)
def _stub_external_singletons(monkeypatch: pytest.MonkeyPatch):
    """Replace heavyweight singletons with inert stubs for ALL tests."""

    dummy = _DummyService()

    # ------------------------------------------------------------------
    # Patch global singletons on the *main* module
    # ------------------------------------------------------------------
    monkeypatch.setattr(app_module, "llm_service_instance", dummy, raising=False)
    monkeypatch.setattr(app_module, "exa_client_instance", dummy, raising=False)

    # ------------------------------------------------------------------
    # Stub ExaClient with minimal behaviour needed by search/shuffle endpoints
    # ------------------------------------------------------------------

    class _StubExaClient:  # noqa: D101 – testing stub
        def __init__(self):
            self._papers: list[Paper] = [
                Paper(
                    id="p1",
                    title="Example Paper One",
                    url="https://example.org/1",
                    authors=["Alice", "Bob"],
                    published_date="2024-01-01",
                ),
                Paper(
                    id="p2",
                    title="Example Paper Two",
                    url="https://example.org/2",
                    authors=["Carol"],
                    published_date="2024-02-01",
                ),
                Paper(
                    id="p3",
                    title="Example Paper Three",
                    url="https://example.org/3",
                ),
            ]

        # ---- Search helpers ------------------------------------------------
        def search_and_cache(self, *a: Any, **kw: Any):  # noqa: D401
            return self._papers

        def url_search_and_cache(self, *a: Any, **kw: Any):  # noqa: D401
            return self._papers

        # ---- Shuffle helpers ----------------------------------------------
        def replace_wildcard(self, *a: Any, **kw: Any):  # noqa: D401
            return self._papers[-1]

        def shuffle_k(self, *a: Any, **kw: Any):  # noqa: D401
            # Simple rotate to simulate shuffle
            self._papers = self._papers[1:] + self._papers[:1]
            return self._papers

        def get_cached_search(self):  # noqa: D401
            return SimpleNamespace(pinned_id="p1")

    stub_exa = _StubExaClient()
    monkeypatch.setattr(app_module, "exa_client_instance", stub_exa, raising=False)

    # Ensure api.deps.get_exa_client returns stub
    import api.deps as deps  # noqa: E402 – defer until after path tweak

    monkeypatch.setattr(deps, "get_exa_client", lambda: stub_exa, raising=False)

    # Ensure config.EXA_API_KEY is non-empty to satisfy guard in search route
    import config as _cfg  # noqa: E402 – late import

    monkeypatch.setattr(_cfg.config, "EXA_API_KEY", "test-key", raising=False)

    # Stub TextExtractor singleton (avoid PDF downloads)
    try:
        te_mod = importlib.import_module("services.paper.text_extractor")
        monkeypatch.setattr(te_mod.TextExtractor, "instance", lambda *a, **k: dummy, raising=False)
    except ModuleNotFoundError:
        pass

    # Stub ReportGenerator heavy methods
    with contextlib.suppress(ModuleNotFoundError):
        rg_mod = importlib.import_module("services.report.report_generator")
        monkeypatch.setattr(
            rg_mod.ReportGenerator,
            "generate_report_html",
            lambda *a, **k: "<p>stub</p>",
            raising=False,
        )
        monkeypatch.setattr(
            rg_mod.ReportGenerator,
            "generate_direct_query_answer",
            lambda *a, **k: "<p>ans</p>",
            raising=False,
        )

    # Stub NotesGenerator to avoid LLM/Docx dependency
    with contextlib.suppress(ModuleNotFoundError):
        ng_mod = importlib.import_module("services.notes.notes_generator")

        async def _fake_generate(*args, **kwargs):  # noqa: D401 – test stub
            out_format = kwargs.get("output_format", "txt")
            if out_format == "docx":
                return (
                    b"docx-bytes",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "notes.docx",
                )
            return ("stub notes", "text/plain", "notes.txt")

        monkeypatch.setattr(ng_mod.NotesGenerator, "generate", _fake_generate, raising=False)

    # Stub out UI error rendering (fasthtml dependency)
    with contextlib.suppress(ModuleNotFoundError):
        mp = importlib.import_module("pages.main_page")
        monkeypatch.setattr(
            mp,
            "render_error_message",
            lambda *a, **k: SimpleNamespace(render=lambda: "<div>err</div>"),
            raising=False,
        )


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Return a shared TestClient instance bound to the FastAPI app."""

    return TestClient(app_module.app)
