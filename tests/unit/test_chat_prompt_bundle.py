"""Unit tests for chat prompt bundle: numbered sources and citation guidance."""

from __future__ import annotations

import sys
import types

from services.chat.chat_runtime import PromptBundle, build_prompt
from services.chat.chat_state import ChatMessage, get_chat_state


def _stub_rag(monkeypatch, hits):
    vs_stub = types.ModuleType("services.paper.vector_store")
    vs_stub.index_exists = lambda pid: hits is not None  # noqa: ARG005
    vs_stub.search = lambda *a, **k: hits or []
    monkeypatch.setitem(sys.modules, "services.paper.vector_store", vs_stub)

    emb_stub = types.ModuleType("services.paper.embedding")
    emb_stub.embed_query = lambda q: [0.0]  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "services.paper.embedding", emb_stub)


async def test_build_prompt_numbers_rag_chunks(monkeypatch, tmp_path) -> None:
    """RAG chunks become [n]-numbered sources the prompt tells the model to cite."""
    hits = [
        {"text": "Alpha chunk about attention.", "section": "Introduction"},
        {"text": "Beta chunk about scaling.", "section": "Methods"},
    ]
    _stub_rag(monkeypatch, hits)

    state = get_chat_state("u-bundle-1")
    state.append("paper-x", ChatMessage(role="user", content="what is attention?"))

    bundle = await build_prompt(state, "paper-x")

    assert isinstance(bundle, PromptBundle)
    assert "[1] Introduction" in bundle.prompt
    assert "[2] Methods" in bundle.prompt
    assert "cite it inline as [n]" in bundle.prompt
    assert [s["n"] for s in bundle.sources] == [1, 2]
    assert bundle.sources[0]["section"] == "Introduction"
    assert bundle.sources[1]["snippet"].startswith("Beta chunk")


async def test_build_prompt_no_rag_no_citation_instruction(
    monkeypatch, tmp_path
) -> None:
    """Without retrieval hits there are no sources and no [n] instructions."""
    _stub_rag(monkeypatch, None)

    state = get_chat_state("u-bundle-2")
    state.append("paper-y", ChatMessage(role="user", content="hello"))

    bundle = await build_prompt(state, "paper-y")

    assert bundle.sources == []
    assert "cite it inline as [n]" not in bundle.prompt


async def test_build_prompt_global_returns_bundle() -> None:
    """The un-grounded global assistant still returns a PromptBundle."""
    state = get_chat_state("u-bundle-3")
    state.append("global", ChatMessage(role="user", content="hi there"))

    bundle = await build_prompt(state, "global")

    assert isinstance(bundle, PromptBundle)
    assert bundle.prompt
    assert bundle.sources == []
    assert bundle.figure_paths == []
