"""LLM error utilities."""

from typing import Optional


class LLMError(Exception):
    """Base exception for all LLM-related issues."""


class LLMProviderError(LLMError):
    """Wraps errors returned directly from a provider SDK/API."""

    def __init__(
        self,
        provider: str,
        message: str,
        original_exception: Optional[Exception] | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.original_exception = original_exception


class LLMRateLimitError(LLMProviderError):
    """Raised when an LLM provider responds with a rate-limit status."""


class LLMConfigurationError(LLMError):
    """Raised when required configuration (e.g. API key) is missing or invalid."""


class LLMResponseError(LLMError):
    """Raised when the provider returns an unexpected or unsafe response."""

    def __init__(
        self, provider: str, message: str, is_safety_issue: bool = False
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.is_safety_issue = is_safety_issue
