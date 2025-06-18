"""Tests for LLMService.generate_completion_safe()."""

from typing import Any, Optional

import pytest

from services.llm_client import LLMProvider, LLMService


class _DummyProvider(LLMProvider):
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail

    async def initialize_client(self, api_key: Optional[str] = None) -> None:
        """Mock initialization."""
        pass  # No-op for dummy

    async def close(self) -> None:
        """Mock close."""
        pass  # No-op for dummy

    async def generate_text(self, *args: Any, **kwargs: Any) -> str:  # noqa: D401 – test stub
        if self.should_fail:
            raise RuntimeError("boom")
        return "ok-response"


@pytest.mark.asyncio
async def test_generate_completion_safe_success() -> None:
    svc = LLMService()
    svc.providers["openai"] = _DummyProvider()
    svc._initialized = True  # Bypass init logic

    res = await svc.generate_completion_safe(
        provider_name="openai",
        model_name="gpt-3.5",
        prompt="hi",
        max_tokens=1,
    )
    assert res == {"ok": True, "content": "ok-response"}


@pytest.mark.asyncio
async def test_generate_completion_safe_failure() -> None:
    svc = LLMService()
    svc.providers["openai"] = _DummyProvider(should_fail=True)
    svc._initialized = True

    res = await svc.generate_completion_safe(
        provider_name="openai",
        model_name="gpt-3.5",
        prompt="hi",
        max_tokens=1,
    )
    assert res["ok"] is False
    assert "Unhandled" in res["content"] or res["content"].startswith("Error:")
