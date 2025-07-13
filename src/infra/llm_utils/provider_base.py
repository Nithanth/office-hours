"""Async LLM provider base class."""

from abc import ABC, abstractmethod
from typing import Any, Optional

from config import config as app_config
from infra.llm_utils.errors import LLMConfigurationError


class LLMProvider(ABC):
    """Abstract base class for an asynchronous LLM provider implementation."""

    _async_client: Optional[Any] = None  # noqa: D401 - class-level placeholder

    def __init__(self, api_key: Optional[str], default_model: str) -> None:
        if not api_key:
            raise LLMConfigurationError(
                f"{self.__class__.__name__} API key is not configured or missing."
            )
        self.api_key = api_key
        self.default_model = default_model
        # Instance-specific SDK client placeholder
        self._async_client: Optional[Any] = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    @abstractmethod
    async def initialize_client(self) -> None:  # noqa: D401 - imperative mood fine
        """Create and store the underlying async SDK client."""

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Return a completion string - subclasses implement provider specifics."""

    @abstractmethod
    async def close(self) -> None:
        """Close network connections / clients if necessary."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _get_effective_temperature(self, temperature: Optional[float]) -> float:
        """Return *temperature* or the global default when *None*."""
        return temperature if temperature is not None else app_config.LLM_TEMPERATURE
