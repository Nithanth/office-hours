"""Tests for LLMService.generate_completion_safe()."""

from typing import Any

import pytest

from services.core.llm_service import LLMService
from services.core.llm_utils.provider_base import LLMProvider


class _DummyProvider(LLMProvider):
    """Minimal test double that optionally raises during generation."""

    def __init__(self, should_fail: bool = False):
        super().__init__(api_key="dummy", default_model="unit-test-model")
        self.should_fail = should_fail

    async def initialize_client(self) -> None:
        """No-op; nothing to initialise for dummy provider."""
        return None

    async def generate_text(self, *args: Any, **kwargs: Any) -> str:  # noqa: D401 – test stub
        if self.should_fail:
            raise RuntimeError("boom")
        return "ok-response"

    async def close(self) -> None:  # noqa: D401 – satisfy abstract base
        """No-op close for dummy provider."""
        return None


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
