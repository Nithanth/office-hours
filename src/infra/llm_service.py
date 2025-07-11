""" """

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional, TypedDict, cast

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Attempt relative import for normal package use
try:
    from ..config import config as app_config
except ImportError:
    # Fallback for direct script execution (e.g., python -m infra.llm_service)
    import os
    import sys

    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if PROJECT_ROOT not in sys.path:
        sys.path.append(PROJECT_ROOT)
    from config import config as app_config  # type: ignore[no-redef]

# Import official client libraries
from anthropic import APIError as AnthropicAPIError
from anthropic import AsyncAnthropic
from anthropic import RateLimitError as AnthropicRateLimitError
from google import genai
from google.api_core.exceptions import GoogleAPIError
from google.api_core.exceptions import ResourceExhausted as GeminiRateLimitError
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI
from openai import RateLimitError as OpenAIRateLimitError
from together import Together

# Setup basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=app_config.LOG_LEVEL.upper(),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

LLMProviderName = Literal["together_ai", "openai", "anthropic", "gemini"]


class LLMResult(TypedDict):
    """Simple result wrapper returned by generate_completion_safe."""

    ok: bool
    content: str


# --- Custom Exceptions ---
class LLMError(Exception):
    """Base exception for LLM-related errors."""

    pass


class LLMProviderError(LLMError):
    """Raised when there's a specific error with an LLM provider's API call."""

    def __init__(
        self, provider: str, message: str, original_exception: Optional[Exception] = None
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.original_exception = original_exception


class LLMRateLimitError(LLMProviderError):
    """Raised when a rate limit is exceeded for an LLM provider."""

    pass


class LLMConfigurationError(LLMError):
    """Raised when there's an issue with LLM configuration (e.g., missing API key)."""

    pass


class LLMResponseError(LLMError):
    """Raised when the LLM response is not as expected."""

    def __init__(self, provider: str, message: str, is_safety_issue: bool = False) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.is_safety_issue = is_safety_issue


# --- LLM Provider Abstract Base Class ---
class LLMProvider(ABC):
    """Abstract base class for an LLM provider."""

    def __init__(self, api_key: Optional[str], default_model: str) -> None:
        if not api_key:
            raise LLMConfigurationError(
                f"{self.__class__.__name__} API key is not configured or missing."
            )
        self.api_key = api_key
        self.default_model = default_model
        self._async_client: Optional[Any] = None  # To be defined by subclasses

    @abstractmethod
    async def initialize_client(self) -> None:
        """Initializes the asynchronous HTTP client for the provider."""
        pass

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
        """Generates text using the LLM.
        Args:
            prompt: The user's input prompt.
            model: The specific model to use (overrides default).
            temperature: The sampling temperature.
            max_tokens: The maximum number of tokens to generate.
            system_prompt: An optional system-level instruction for the model.
            **kwargs: Additional provider-specific parameters.
        Returns:
            The generated text.
        Raises:
            LLMProviderError: For API-specific errors.
            LLMRateLimitError: For rate limiting errors.
            LLMConfigurationError: For configuration issues.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cleans up any resources, like HTTP clients."""
        pass

    def _get_effective_temperature(self, temperature: Optional[float]) -> float:
        return temperature if temperature is not None else app_config.LLM_TEMPERATURE


# --- Concrete LLM Provider Implementations ---


class TogetherAIProvider(LLMProvider):
    async def initialize_client(self) -> None:
        if self._async_client is None:
            logger.debug(f"Initializing TogetherAI client for {self.default_model}")
            self._async_client = Together(api_key=self.api_key)

    async def close(self) -> None:
        # TogetherAI client doesn't have an explicit close method for the sync client
        logger.debug("TogetherAI client does not require explicit closing.")
        self._async_client = None
        await asyncio.sleep(0)  # To make it awaitable if needed by interface

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        primary_model_to_use = model or self.default_model
        try:
            logger.info(
                f"Attempting TogetherAI generation with primary model: {primary_model_to_use}"
            )
            return await self._attempt_generation_with_model(
                prompt, primary_model_to_use, temperature, max_tokens, system_prompt, **kwargs
            )
        except (
            LLMProviderError,
            RetryError,
        ) as e_primary:  # RetryError wraps the last attempt's LLMProviderError
            logger.warning(
                f"Primary TogetherAI model {primary_model_to_use} failed: {e_primary}. Attempting fallback."
            )
            fallback_model_to_use = app_config.TOGETHER_FALLBACK_MODEL
            if not fallback_model_to_use:
                logger.error(
                    f"No fallback model configured for TogetherAI. Primary failure: {e_primary}"
                )
                raise e_primary  # Re-raise the original error if no fallback
            if fallback_model_to_use == primary_model_to_use:
                logger.warning(
                    f"Fallback model {fallback_model_to_use} is the same as primary. Not re-attempting."
                )
                raise e_primary  # Re-raise the original error

            try:
                logger.info(
                    f"Attempting TogetherAI generation with fallback model: {fallback_model_to_use}"
                )
                return await self._attempt_generation_with_model(
                    prompt, fallback_model_to_use, temperature, max_tokens, system_prompt, **kwargs
                )
            except (LLMProviderError, RetryError) as e_fallback:
                logger.error(
                    f"Fallback TogetherAI model {fallback_model_to_use} also failed: {e_fallback}. Both primary and fallback attempts exhausted."
                )
                raise e_fallback  # Raise the fallback error as the final one

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=3, max=20),
        retry=retry_if_exception_type(LLMRateLimitError),
        reraise=True,
    )
    async def _attempt_generation_with_model(
        self,
        prompt: str,
        model_to_use: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        await self.initialize_client()
        assert self._async_client is not None, "TogetherAI client not initialized"
        actual_temperature = self._get_effective_temperature(temperature)

        _prompt_parts = []
        if system_prompt:
            _prompt_parts.append({"role": "system", "content": system_prompt})
        _prompt_parts.append({"role": "user", "content": prompt})

        logger.debug(
            f"TogetherAI request: Model={model_to_use}, Temp={actual_temperature}, MaxTokens={max_tokens}"
        )

        try:
            # Use asyncio.to_thread to run the synchronous Together client in a separate thread
            response = await asyncio.to_thread(
                self._async_client.chat.completions.create,
                model=model_to_use,
                messages=_prompt_parts,
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            # Extract the response text
            if (
                not response.choices
                or not response.choices[0].message
                or response.choices[0].message.content is None
            ):
                raise LLMProviderError(
                    "together_ai",
                    f"Invalid response structure from API for model {model_to_use}.",
                )
            return str(response.choices[0].message.content).strip()
        except Exception as e:
            # Handle different types of exceptions from the Together client
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"TogetherAI rate limit hit for model {model_to_use}.")
                raise LLMRateLimitError(
                    "together_ai", f"Rate limit exceeded for model {model_to_use}.", e
                ) from e

            # Log the error with detailed information
            logger.error(f"TogetherAI error for model {model_to_use}: {type(e).__name__}: {str(e)}")

            # Create a user-friendly error message
            error_message = f"API request failed for model {model_to_use}: {str(e)}"
            raise LLMProviderError("together_ai", error_message, e) from e


class OpenAIProvider(LLMProvider):
    async def initialize_client(self) -> None:
        if self._async_client is None:
            logger.debug(f"Initializing OpenAI client for {self.default_model}")
            self._async_client = AsyncOpenAI(api_key=self.api_key)

    async def close(self) -> None:
        if self._async_client:
            logger.debug("Closing OpenAI client.")
            await self._async_client.close()
            self._async_client = None

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        await self.initialize_client()
        assert self._async_client is not None, "OpenAI client not initialized"
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        _prompt_parts = []
        if system_prompt:
            _prompt_parts.append({"role": "system", "content": system_prompt})
        _prompt_parts.append({"role": "user", "content": prompt})

        logger.debug(
            f"OpenAI request: Model={selected_model}, Temp={actual_temperature}, MaxTokens={max_tokens}"
        )
        try:
            response = await self._async_client.chat.completions.create(
                model=selected_model,
                messages=_prompt_parts,
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if (
                not response.choices
                or not response.choices[0].message
                or response.choices[0].message.content is None
            ):
                raise LLMProviderError("openai", "Invalid response structure from API.", None)
            return str(response.choices[0].message.content).strip()
        except OpenAIRateLimitError as e:
            logger.warning(f"OpenAI rate limit hit for model {selected_model}.")
            raise LLMRateLimitError("openai", "Rate limit exceeded.", e) from e
        except OpenAIAPIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise LLMProviderError("openai", f"API request failed: {e}", e) from e
        except Exception as e:  # Catch any other unexpected errors
            logger.error(f"Unexpected error with OpenAI provider: {e}")
            raise LLMProviderError("openai", f"An unexpected error occurred: {e}", e) from e

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (OpenAIAPIError, LLMRateLimitError)
        ),  # Catch generic APIError for retries too
        reraise=True,
    )
    async def _generate_text_with_retry(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        return await self.generate_text(
            prompt, model, temperature, max_tokens, system_prompt, **kwargs
        )


class AnthropicProvider(LLMProvider):
    async def initialize_client(self) -> None:
        if self._async_client is None:
            logger.debug(f"Initializing Anthropic client for {self.default_model}")
            self._async_client = AsyncAnthropic(api_key=self.api_key)

    async def close(self) -> None:
        if self._async_client:
            logger.debug("Closing Anthropic client.")
            await self._async_client.close()
            self._async_client = None

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        _messages = [{"role": "user", "content": prompt}]

        logger.debug(
            f"Anthropic request: Model={selected_model}, Temp={actual_temperature}, MaxTokens={max_tokens}"
        )
        try:
            response = await self._async_client.messages.create(  # type: ignore
                model=selected_model,
                messages=_messages,
                system=system_prompt,  # Pass system prompt here
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if not response.content or not response.content[0].text:
                raise LLMProviderError("anthropic", "Invalid response structure from API.", None)
            return str(response.content[0].text).strip()
        except AnthropicRateLimitError as e:
            logger.warning(f"Anthropic rate limit hit for model {selected_model}.")
            raise LLMRateLimitError("anthropic", "Rate limit exceeded.", e) from e
        except AnthropicAPIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise LLMProviderError("anthropic", f"API request failed: {e}", e) from e
        except Exception as e:
            logger.error(f"Unexpected error with Anthropic provider: {e}")
            raise LLMProviderError("anthropic", f"An unexpected error occurred: {e}", e) from e

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(
            (AnthropicAPIError, LLMRateLimitError)
        ),  # Catch generic APIError
        reraise=True,
    )
    async def _generate_text_with_retry(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        return await self.generate_text(
            prompt, model, temperature, max_tokens, system_prompt, **kwargs
        )


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini models using the new google-genai SDK."""

    _async_client: Optional[Any] = None

    def __init__(self, api_key: Optional[str], default_model: str):
        super().__init__(api_key, default_model)

    async def initialize_client(self) -> None:
        """Initializes the synchronous client for the Gemini API."""
        if self.api_key:
            # The client is synchronous, the .aio property provides async methods
            self._async_client = genai.Client(api_key=self.api_key)
        else:
            logger.warning("Gemini API key not found. GeminiProvider will be unavailable.")

    async def close(self) -> None:
        """Releases Gemini client resources."""
        # The new genai.Client doesn't have an explicit close method.
        # Setting to None allows for garbage collection and re-initialization.
        self._async_client = None

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        if not self._async_client:
            raise LLMProviderError(provider="gemini", message="GeminiProvider is not initialized.")

        model_to_use = model or self.default_model

        # The installed google-genai version does not support generation_config.
        # We must omit it for the call to succeed. Temperature and max_tokens will be ignored.

        # Prepend system prompt to the main prompt if it exists
        final_prompt = (
            f"System Prompt: {system_prompt}\n\n---\n\n{prompt}" if system_prompt else prompt
        )

        try:
            response = await self._async_client.aio.models.generate_content(
                model=model_to_use,
                contents=final_prompt,
            )
            # The response object has a text property for simple text responses
            return cast(str, response.text)
        except GoogleAPIError as e:
            logger.error(f"Gemini API error for model {model_to_use}: {e}")
            if isinstance(e, GeminiRateLimitError):
                raise LLMRateLimitError(
                    provider="gemini",
                    message=f"Gemini rate limit exceeded for model {model_to_use}.",
                ) from e
            raise LLMProviderError(
                provider="gemini",
                message=f"Gemini API error for model {model_to_use}: {e}",
            ) from e
        except Exception as e:
            logger.error(f"An unexpected error occurred with Gemini model {model_to_use}: {e}")
            raise LLMProviderError(
                provider="gemini",
                message=f"An unexpected error in Gemini provider for model {model_to_use}: {e}",
            ) from e


# --- LLM Service Orchestrator ---
class LLMService:
    """Orchestrates LLM calls to various providers."""

    _instance = None
    _initialized = False

    def __new__(cls, *args: Any, **kwargs: Any) -> "LLMService":
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self) -> None:
        if LLMService._initialized:
            return

        self.logger = logging.getLogger(self.__class__.__name__)
        self.providers: dict[LLMProviderName, LLMProvider] = {}

        # Helper to look up a sensible default model for each provider
        def _get_default_model_for_provider(provider: LLMProviderName) -> str | None:
            # If the configured DEFAULT_MODEL_KEY belongs to this provider - use it.
            default_info = app_config.AVAILABLE_MODELS.get(app_config.DEFAULT_MODEL_KEY)
            if default_info and default_info["provider"] == provider:
                return str(default_info["id"])
            # Otherwise return the first model we find for that provider.
            for info in app_config.AVAILABLE_MODELS.values():
                if info["provider"] == provider:
                    return str(info["id"])
            self.logger.warning("No model found for provider %s in AVAILABLE_MODELS.", provider)
            return None

        self.provider_configs: dict[LLMProviderName, dict[str, Any]] = {
            "together_ai": {
                "api_key": app_config.TOGETHER_API_KEY,
                "default_model": _get_default_model_for_provider("together_ai"),
                "ProviderClass": TogetherAIProvider,
            },
            "openai": {
                "api_key": app_config.OPENAI_API_KEY,
                "default_model": _get_default_model_for_provider("openai"),
                "ProviderClass": OpenAIProvider,
            },
            "anthropic": {
                "api_key": app_config.ANTHROPIC_API_KEY,
                "default_model": _get_default_model_for_provider("anthropic"),
                "ProviderClass": AnthropicProvider,
            },
            "gemini": {
                "api_key": app_config.GEMINI_API_KEY,
                "default_model": _get_default_model_for_provider("gemini"),
                "ProviderClass": GeminiProvider,
            },
        }

    async def initialize_providers(self) -> None:
        """Initializes all configured LLM providers asynchronously."""
        if self._initialized:
            return

        self.logger.info("Initializing all LLM providers...")
        for provider_name, config in self.provider_configs.items():
            api_key = config.get("api_key")
            ProviderClass = config["ProviderClass"]
            default_model_id = config["default_model"]

            if api_key:
                try:
                    provider_instance = ProviderClass(
                        api_key=api_key, default_model=default_model_id
                    )
                    await provider_instance.initialize_client()
                    self.providers[provider_name] = provider_instance
                    self.logger.info(
                        f"{ProviderClass.__name__} initialized with model '{default_model_id}'."
                    )
                except Exception as e:
                    self.logger.error(f"Failed to initialize {ProviderClass.__name__}: {e}")
            else:
                self.logger.warning(
                    f"API key for {provider_name} not found. {ProviderClass.__name__} will not be available."
                )

        self._initialized = True
        self.logger.info("LLMService initialization complete.")

    def is_provider_initialized(self, provider_name: LLMProviderName) -> bool:
        """Checks if the specified LLM provider is initialized and available."""
        is_init = provider_name in self.providers
        if not is_init:
            self.logger.warning(
                f"LLMService.is_provider_initialized: Provider '{provider_name}' not found in initialized providers. API key might be missing or setup failed."
            )
        return is_init

    async def close_all_providers(self) -> None:
        """Closes all initialized provider clients."""
        logger.info("Closing all LLM provider clients.")
        for provider_name, provider in self.providers.items():
            try:
                await provider.close()
                logger.debug(f"Successfully closed {provider_name} provider.")
            except Exception as e:
                self.logger.error(f"Error closing provider {provider_name}: {e}", exc_info=True)
        self._initialized = False
        self.providers.clear()

    async def generate_completion_stream(
        self,
        provider_name: LLMProviderName,
        model_name: str,
        prompt: str,
        max_tokens: int,
        stop_sequences: Optional[list[str]] | None = None,
        temperature: Optional[float] | None = None,
        system_prompt: Optional[str] | None = None,
    ) -> AsyncIterator[str]:
        """Yield tokens incrementally if the provider supports native streaming.

        Falls back to a whitespace split on the full completion when native
        streaming is unavailable or fails.
        """
        # Ensure providers ready
        if not self._initialized:
            await self.initialize_providers()

        provider = self.providers.get(provider_name)
        if provider is None:
            raise LLMConfigurationError(f"Provider {provider_name} not configured.")

        await provider.initialize_client()

        async def _fallback_stream() -> AsyncIterator[str]:
            full_resp = await self.generate_completion(
                provider_name,
                model_name,
                prompt,
                max_tokens,
                stop_sequences=stop_sequences,
                temperature=temperature,
                system_prompt=system_prompt,
            )
            for token in full_resp.split():
                yield token

        # OpenAI native streaming
        if provider_name == "openai":
            try:
                from openai import AsyncOpenAI  # Local import to avoid hard dep when key missing

                openai_client = cast(AsyncOpenAI, provider._async_client)
                if openai_client is None:
                    return _fallback_stream()
                # Build messages
                msgs: list[dict[str, str]] = []
                if system_prompt:
                    msgs.append({"role": "system", "content": system_prompt})
                msgs.append({"role": "user", "content": prompt})

                effective_temp: float = temperature if temperature is not None else 0.7

                async def _openai_stream() -> AsyncIterator[str]:
                    try:
                        resp = await openai_client.chat.completions.create(
                            model=model_name,
                            messages=msgs,  # type: ignore[arg-type]
                            max_tokens=max_tokens,
                            temperature=effective_temp,
                            stop=stop_sequences,
                            stream=True,
                        )
                        resp_iter = cast(AsyncIterator[Any], resp)
                        async for chunk in resp_iter:
                            delta = chunk.choices[0].delta
                            if delta and delta.content is not None:
                                yield str(delta.content)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "OpenAI streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _openai_stream()
            except Exception:
                return _fallback_stream()

        # Anthropic native streaming
        if provider_name == "anthropic":
            try:
                from anthropic import AsyncAnthropic  # Import lazily

                anth_client = cast(AsyncAnthropic, provider._async_client)
                if anth_client is None:
                    return _fallback_stream()

                async def _anthropic_stream() -> AsyncIterator[str]:
                    try:
                        effective_temp: float = temperature if temperature is not None else 0.7
                        stream_iter = cast(
                            AsyncIterator[Any],
                            await anth_client.messages.create(
                                model=model_name,
                                max_tokens=max_tokens,
                                temperature=effective_temp,
                                system=system_prompt or "",
                                messages=[{"role": "user", "content": prompt}],
                                stream=True,
                            ),
                        )
                        async for event_any in stream_iter:
                            event = cast(Any, event_any)
                            if hasattr(event, "delta") and getattr(event.delta, "text", None):
                                yield str(event.delta.text)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "Anthropic streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _anthropic_stream()
            except Exception:
                return _fallback_stream()

        # Together AI native streaming
        if provider_name == "together_ai":
            try:
                from together import AsyncTogether  # Lazy import

                tg_client = cast(AsyncTogether, provider._async_client)
                if tg_client is None:
                    tg_client = AsyncTogether(api_key=app_config.TOGETHER_API_KEY)
                    provider._async_client = tg_client  # cache for reuse

                async def _together_stream() -> AsyncIterator[str]:
                    try:
                        msgs: list[dict[str, str]] = []
                        if system_prompt:
                            msgs.append({"role": "system", "content": system_prompt})
                        msgs.append({"role": "user", "content": prompt})

                        eff_temp: float = temperature if temperature is not None else 0.7

                        stream = await tg_client.chat.completions.create(
                            model=model_name,
                            messages=msgs,
                            max_tokens=max_tokens,
                            temperature=eff_temp,
                            stop=stop_sequences,
                            stream=True,
                        )
                        async for chunk in cast(AsyncIterator[Any], stream):
                            delta = getattr(chunk.choices[0], "delta", None)
                            if delta and getattr(delta, "content", None):
                                yield str(delta.content)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "TogetherAI streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _together_stream()
            except Exception:
                return _fallback_stream()

        # Gemini native streaming
        if provider_name == "gemini":
            try:
                from google import genai  # Lazy import - keeps optional dependency lightweight

                gm_client = cast(Any, provider._async_client)
                if gm_client is None:
                    gm_client = genai.Client(api_key=app_config.GEMINI_API_KEY)
                    provider._async_client = gm_client  # cache for reuse

                async def _gemini_stream() -> AsyncIterator[str]:
                    try:
                        final_prompt = (
                            f"System Prompt: {system_prompt}\n\n---\n\n{prompt}"
                            if system_prompt
                            else prompt
                        )

                        # The gemini sdk exposes async helpers under the `aio` facade
                        aio_client = gm_client.aio
                        stream_iter = cast(
                            AsyncIterator[Any],
                            await aio_client.models.generate_content_stream(
                                model=model_name,
                                contents=[final_prompt],
                            ),
                        )

                        async for chunk_any in stream_iter:
                            chunk = cast(Any, chunk_any)
                            text_part = getattr(chunk, "text", None)
                            if text_part:
                                yield str(text_part)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "Gemini streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _gemini_stream()
            except Exception:
                return _fallback_stream()

    async def generate_completion(
        self,
        provider_name: LLMProviderName,
        model_name: str,
        prompt: str,
        max_tokens: int,
        stop_sequences: Optional[list[str]] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,  # Added system_prompt
    ) -> str:
        if not self._initialized:
            await self.initialize_providers()  # Ensure initialized

        provider = self.providers.get(provider_name)
        if not provider:
            self.logger.error(f"Provider {provider_name} not initialized or not found.")
            # Fallback or error handling strategy
            # Try to use the application's default model's provider if current one fails?
            # For now, return error message.
            return f"Error: LLM Provider '{provider_name}' is not available or not configured."

        try:
            # The model_name passed here is the one selected by the user/application, not the provider's internal default.
            return await provider.generate_text(
                prompt=prompt,
                model=model_name,  # If None, provider uses its default
                temperature=temperature,  # If None, provider uses global or its default
                max_tokens=max_tokens,
                system_prompt=system_prompt,  # Pass system_prompt
                **{},
            )
        except RetryError as e:  # Raised by tenacity after max retries
            logger.error(
                f"LLM call failed after multiple retries for provider {provider.__class__.__name__}: {e}"
            )
            return f"LLM call failed after max retries with {provider.__class__.__name__}. Error: {e.last_attempt.exception()}"  # Example of returning error string
        except LLMResponseError as e:  # Specific to bad responses, not necessarily provider failure
            self.logger.error(f"LLM response error from {provider_name}: {e}")
            return f"Error: LLM response error from {provider_name}. Details: {e}"
        except LLMError as e:  # Catch our custom LLM errors
            logger.error(f"LLM Error with {provider.__class__.__name__}: {e}")
            return f"Error: LLM Error with {provider_name}. Details: {e}"
        except Exception as e:  # Catch-all for unexpected errors
            self.logger.exception(
                f"An unexpected error occurred with {provider_name} and model {model_name}"
            )
            return f"Error: An unexpected error occurred with {provider_name} and model {model_name}. Details: {e}"

    # ---------------------------------------------------------------------------
    # Structured result helper (Phase-2 incremental, non-breaking)
    # ---------------------------------------------------------------------------

    class LLMResult(TypedDict):
        ok: bool  # True when the provider call succeeded
        content: str  # Either the LLM response text or an error message

    # ------------------------------------------------------------------
    # Safe variant returning structured result (non-breaking addition)
    # ------------------------------------------------------------------
    async def generate_completion_safe(
        self,
        provider_name: LLMProviderName,
        model_name: str,
        prompt: str,
        max_tokens: int,
        stop_sequences: Optional[list[str]] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
    ) -> "LLMResult":
        """Wrapper around ``generate_completion`` that never raises and instead
        returns a simple dict::

            {"ok": bool, "content": str}

        This allows UI layers to branch on ``ok`` without parsing error
        strings.  Existing callers can migrate at their own pace.
        """

        try:
            content = await self.generate_completion(
                provider_name=provider_name,
                model_name=model_name,
                prompt=prompt,
                max_tokens=max_tokens,
                stop_sequences=stop_sequences,
                temperature=temperature,
                system_prompt=system_prompt,
            )
            # generate_completion already converts errors to string messages
            is_ok = not content.startswith("Error:")
            return {"ok": is_ok, "content": content}
        except Exception as e:  # catch any leakage
            return {"ok": False, "content": f"Unhandled exception in LLM call: {e}"}


# --- Context Manager for LLMService ---


@asynccontextmanager
async def get_llm_service() -> AsyncIterator[LLMService]:
    """
    Provides an LLMService instance.
    In a FastAPI app context, initialization/cleanup is best handled by lifespan events.
    This manager can still ensure initialization/cleanup for other contexts (e.g., tests).
    """
    service = LLMService()
    if not service._initialized:
        logger.debug("get_llm_service: Initializing providers within context manager.")
        await service.initialize_providers()
    try:
        yield service
    finally:
        logger.debug("get_llm_service: Closing providers within context manager.")
        await service.close_all_providers()


# --- Example Usage (for direct testing of this module) ---
async def main_test() -> None:
    print("Running LLM Service example...")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger.info(
        "Configured default provider from env: %s",
        getattr(app_config, "DEFAULT_LLM_PROVIDER", "unknown"),
    )
    logger.info(f"OpenAI Key set: {'Yes' if app_config.OPENAI_API_KEY else 'No'}")
    logger.info(f"Together Key set: {'Yes' if app_config.TOGETHER_API_KEY else 'No'}")
    logger.info(f"Anthropic Key set: {'Yes' if app_config.ANTHROPIC_API_KEY else 'No'}")
    logger.info(f"Gemini Key set: {'Yes' if app_config.GEMINI_API_KEY else 'No'}")

    sample_prompt = "Explain the concept of a Large Language Model in one sentence."

    async with get_llm_service() as llm_service:
        # llm_service should be initialized by the context manager now
        if not llm_service.providers:
            logger.error("No LLM providers are configured with API keys. Exiting example.")
            return

        logger.info(
            "\n--- Testing with Default Provider (%s or fallback) ---",
            getattr(app_config, "DEFAULT_LLM_PROVIDER", "openai"),
        )
        default_provider_any = getattr(app_config, "DEFAULT_LLM_PROVIDER", "openai")
        result_default = await llm_service.generate_completion(
            cast(LLMProviderName, default_provider_any),
            getattr(app_config, "DEFAULT_MODEL_KEY", "gpt-3.5-turbo"),
            sample_prompt,
            2048,
        )
        if result_default:
            logger.info(f"Default Provider Result: {result_default}")
        else:
            logger.warning("Default provider call returned no result or an error.")

        # Test specific providers if configured
        test_providers: list[LLMProviderName] = ["openai", "together_ai", "anthropic", "gemini"]
        for provider_name_to_test in test_providers:
            if provider_name_to_test in llm_service.providers:
                logger.info(f"\n--- Testing with {provider_name_to_test.upper()} Provider ---")
                # Example of overriding model for TogetherAI
                model_override = None
                if provider_name_to_test == "together_ai":
                    model_override = (
                        "mistralai/Mixtral-8x7B-Instruct-v0.1"  # A different model for testing
                    )

                actual_model_to_test = model_override
                if actual_model_to_test is None:
                    provider_instance = llm_service.providers.get(provider_name_to_test)
                    if provider_instance:  # Should always be true if in llm_service.providers
                        actual_model_to_test = provider_instance.default_model
                    else:  # Should not happen given the outer if condition
                        logger.error(
                            f"Provider {provider_name_to_test} not found in initialized providers during test."
                        )
                        continue  # Skip this test iteration

                result_specific = await llm_service.generate_completion(
                    provider_name_to_test,
                    actual_model_to_test,  # Now guaranteed to be str or we skipped
                    sample_prompt,
                    2048,
                    temperature=0.7,
                    stop_sequences=["\n\n", "\n"],
                )
                if result_specific:
                    logger.info(f"{provider_name_to_test.upper()} Result: {result_specific}")
                else:
                    logger.warning(
                        f"{provider_name_to_test.upper()} call returned no result or an error."
                    )
            else:
                logger.info(
                    f"\n--- {provider_name_to_test.upper()} Provider not configured, skipping test. ---"
                )


if __name__ == "__main__":
    # To run this example: python -m infra.llm_service # or python -m tests.integration.llm_client
    asyncio.run(main_test())
