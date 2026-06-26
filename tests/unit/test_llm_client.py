"""Tests for the LLM provider error hierarchy and provider base behaviour.

The conftest stubs ``services.core.llm_utils.*`` in ``sys.modules`` to break
circular imports, so we test against those stubs.  Provider-level ``generate_text``
behaviour is exercised via integration tests (test_chat, test_search, etc.).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.core.llm_utils.errors import (
    LLMConfigurationError,
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
)
from services.core.llm_utils.provider_base import LLMProvider
from services.core.llm_utils.providers import (
    AnthropicProvider,
    OpenAIProvider,
)


# ===================================================================
# 1. Error class construction & hierarchy
# ===================================================================


class TestErrorClasses:
    """Verify error hierarchy and attribute storage."""

    def test_llm_error_is_base_exception(self) -> None:
        err = LLMError("base error")
        assert isinstance(err, Exception)
        assert str(err) == "base error"

    def test_llm_provider_error_attributes(self) -> None:
        orig = ValueError("upstream")
        err = LLMProviderError("openai", "something broke", original_exception=orig)
        assert err.provider == "openai"
        assert err.original_exception is orig
        assert "[openai]" in str(err)
        assert "something broke" in str(err)

    def test_llm_provider_error_is_llm_error(self) -> None:
        err = LLMProviderError("test", "msg")
        assert isinstance(err, LLMError)

    def test_llm_provider_error_no_original(self) -> None:
        err = LLMProviderError("anthropic", "fail")
        assert err.original_exception is None

    def test_llm_rate_limit_error_is_provider_error(self) -> None:
        err = LLMRateLimitError("openai", "429", original_exception=None)
        assert isinstance(err, LLMProviderError)
        assert isinstance(err, LLMError)
        assert err.provider == "openai"

    def test_llm_configuration_error_is_llm_error(self) -> None:
        err = LLMConfigurationError("missing key")
        assert isinstance(err, LLMError)
        assert "missing key" in str(err)

    def test_llm_response_error_default(self) -> None:
        err = LLMResponseError("gemini", "bad output")
        assert err.provider == "gemini"
        assert err.is_safety_issue is False
        assert "[gemini]" in str(err)

    def test_llm_response_error_safety_flag(self) -> None:
        err = LLMResponseError("gemini", "blocked", is_safety_issue=True)
        assert err.is_safety_issue is True

    def test_rate_limit_inherits_from_provider_error(self) -> None:
        """Catch LLMProviderError should also catch LLMRateLimitError."""
        err = LLMRateLimitError("x", "rate limited")
        assert isinstance(err, LLMProviderError)

    def test_configuration_error_not_provider_error(self) -> None:
        """Config errors are not provider errors — different catch semantics."""
        err = LLMConfigurationError("bad config")
        assert not isinstance(err, LLMProviderError)


# ===================================================================
# 2. Provider base class (via conftest stub)
# ===================================================================


class TestProviderBase:
    """Test LLMProvider stub behaviour."""

    def test_provider_stub_accepts_kwargs(self) -> None:
        """The conftest LLMProvider stub should accept api_key/default_model."""
        p = LLMProvider(api_key="sk-test", default_model="gpt-4")
        assert p.api_key == "sk-test"
        assert p.default_model == "gpt-4"

    @pytest.mark.asyncio
    async def test_provider_generate_text(self) -> None:
        """Stub provider should return a string from generate_text."""
        p = LLMProvider(api_key="k", default_model="m")
        result = await p.generate_text("hello")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_provider_close_is_noop(self) -> None:
        """Stub close should not raise."""
        p = LLMProvider(api_key="k", default_model="m")
        await p.close()


# ===================================================================
# 3. Provider instantiation (stub-level)
# ===================================================================


class TestProviderStubs:
    """Test that conftest provider stubs are callable and return stubs."""

    def test_openai_provider_instantiation(self) -> None:
        p = OpenAIProvider(api_key="sk-fake", default_model="gpt-4")
        assert p.api_key == "sk-fake"

    def test_anthropic_provider_instantiation(self) -> None:
        p = AnthropicProvider(api_key="sk-ant-fake", default_model="claude-3")
        assert p.api_key == "sk-ant-fake"

    @pytest.mark.asyncio
    async def test_openai_provider_generate_text_returns_string(self) -> None:
        p = OpenAIProvider(api_key="sk-fake", default_model="gpt-4")
        result = await p.generate_text("hello")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_anthropic_provider_generate_text_returns_string(self) -> None:
        p = AnthropicProvider(api_key="sk-fake", default_model="claude-3")
        result = await p.generate_text("hello")
        assert isinstance(result, str)
