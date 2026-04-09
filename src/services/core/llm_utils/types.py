"""Shared enums and typed helpers used by LLM utilities."""

from dataclasses import dataclass
from enum import Enum
from typing import TypedDict


class ProviderName(str, Enum):
    TOGETHER_AI = "together_ai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    XAI = "xai"


class LLMResult(TypedDict):
    """Return signature for helper that never raises."""

    ok: bool
    content: str


@dataclass(slots=True)
class ProviderConfig:
    api_key: str | None
    default_model: str | None
    class_: type  # concrete subclass of LLMProvider; kept untyped to avoid circular import
