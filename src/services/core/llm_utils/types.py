"""Shared enums and typed helpers used by LLM utilities."""

from enum import StrEnum
from typing import TypedDict


class ProviderName(StrEnum):
    TOGETHER_AI = "together_ai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    XAI = "xai"
    OPENROUTER = "openrouter"


class LLMResult(TypedDict):
    """Return signature for helper that never raises."""

    ok: bool
    content: str
