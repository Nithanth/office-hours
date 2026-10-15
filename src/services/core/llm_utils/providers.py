"""Async wrappers for TogetherAI, OpenAI, Anthropic, Gemini, and xAI SDKs."""

import asyncio
from typing import Any, Optional, cast

from anthropic import (
    APIError as AnthropicAPIError,
)
from anthropic import (
    AsyncAnthropic,
)
from anthropic import (
    RateLimitError as AnthropicRateLimitError,
)
from google import genai
from loguru import logger
from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI
from openai import RateLimitError as OpenAIRateLimitError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Third-party SDK clients
from together import Together

from config import config as app_config
from services.core.llm_utils.errors import (
    LLMProviderError,
    LLMRateLimitError,
)
from services.core.llm_utils.provider_base import LLMProvider


class TogetherAIProvider(LLMProvider):
    """TogetherAI chat-completion provider."""

    _async_client: Optional[Together] = None

    async def initialize_client(self) -> None:
        """Lazily create and cache the TogetherAI SDK client."""
        if self._async_client is None:
            logger.info("Initializing TogetherAI client for {}", self.default_model)
            self._async_client = Together(api_key=self.api_key)

    async def close(self) -> None:
        """No-op close to keep interface parity across providers."""
        logger.debug("TogetherAI client does not require explicit closing.")
        self._async_client = None
        await asyncio.sleep(0)

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate text, with automatic fallback to the configured backup model."""
        primary_model_to_use = model or self.default_model
        try:
            logger.info("TogetherAI generation", model=primary_model_to_use)
            return cast(
                str,
                await self._attempt_generation_with_model(
                    prompt,
                    primary_model_to_use,
                    temperature,
                    max_tokens,
                    system_prompt,
                    **kwargs,
                ),
            )
        except (LLMProviderError, RetryError) as e_primary:
            logger.warning("TogetherAI primary model failed", error=str(e_primary))
            fallback_model_to_use = app_config.TOGETHER_FALLBACK_MODEL
            if (
                not fallback_model_to_use
                or fallback_model_to_use == primary_model_to_use
            ):
                raise e_primary
            logger.info("TogetherAI fallback model", model=fallback_model_to_use)
            return cast(
                str,
                await self._attempt_generation_with_model(
                    prompt,
                    fallback_model_to_use,
                    temperature,
                    max_tokens,
                    system_prompt,
                    **kwargs,
                ),
            )

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
        """Internal helper wrapped by tenacity for retry & rate-limit handling."""
        await self.initialize_client()
        assert self._async_client is not None
        actual_temperature = self._get_effective_temperature(temperature)

        messages_payload: list[dict[str, str]] = []
        if system_prompt:
            messages_payload.append({"role": "system", "content": system_prompt})
        messages_payload.append({"role": "user", "content": prompt})

        try:
            response = await asyncio.to_thread(
                self._async_client.chat.completions.create,
                model=model_to_use,
                messages=messages_payload,
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if not response.choices or response.choices[0].message.content is None:
                raise LLMProviderError("together_ai", "Invalid response structure")
            return str(response.choices[0].message.content).strip()
        except Exception as e:
            if "rate limit" in str(e).lower() or "429" in str(e):
                raise LLMRateLimitError("together_ai", "Rate limit exceeded", e) from e
            raise LLMProviderError("together_ai", str(e), e) from e


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions provider (Async)."""

    _async_client: Optional[AsyncOpenAI] = None

    async def initialize_client(self) -> None:
        """Instantiate the async OpenAI client once per provider instance."""
        if self._async_client is None:
            logger.debug("Initializing OpenAI client for {}", self.default_model)
            self._async_client = AsyncOpenAI(api_key=self.api_key)

    async def close(self) -> None:
        """Explicitly close underlying HTTP session."""
        if self._async_client:
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
        """Return a chat completion using the chosen OpenAI model."""
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        messages_payload: list[dict[str, str]] = []
        if system_prompt:
            messages_payload.append({"role": "system", "content": system_prompt})
        messages_payload.append({"role": "user", "content": prompt})

        # mypy: after initialise, we know _async_client is set.
        assert self._async_client is not None
        client = self._async_client

        try:
            response = await client.chat.completions.create(
                model=selected_model,
                messages=cast(list[Any], messages_payload),
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if not response.choices or response.choices[0].message.content is None:
                raise LLMProviderError("openai", "Invalid response structure")
            return str(response.choices[0].message.content).strip()
        except OpenAIRateLimitError as e:
            raise LLMRateLimitError("openai", "Rate limit exceeded", e) from e
        except OpenAIAPIError as e:
            raise LLMProviderError("openai", f"API request failed: {e}", e) from e
        except Exception as e:
            raise LLMProviderError("openai", f"Unexpected error: {e}", e) from e


class OpenRouterProvider(LLMProvider):
    """OpenRouter provider — OpenAI-compatible API with a custom base_url.

    OpenRouter routes to 400+ models (GLM, Qwen, DeepSeek, etc.) through a
    single endpoint.  Model IDs use the ``author/slug`` format (e.g.
    ``z-ai/glm-5.2``).
    """

    _async_client: Optional[AsyncOpenAI] = None
    _BASE_URL = "https://openrouter.ai/api/v1"

    async def initialize_client(self) -> None:
        """Instantiate the async OpenAI client pointed at OpenRouter."""
        if self._async_client is None:
            logger.debug("Initializing OpenRouter client for {}", self.default_model)
            self._async_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self._BASE_URL,
            )

    async def close(self) -> None:
        """Explicitly close underlying HTTP session."""
        if self._async_client:
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
        """Return a chat completion via OpenRouter (OpenAI-compatible)."""
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        messages_payload: list[dict[str, str]] = []
        if system_prompt:
            messages_payload.append({"role": "system", "content": system_prompt})
        messages_payload.append({"role": "user", "content": prompt})

        assert self._async_client is not None
        client = self._async_client

        try:
            response = await client.chat.completions.create(
                model=selected_model,
                messages=cast(list[Any], messages_payload),
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            if not response.choices or response.choices[0].message.content is None:
                raise LLMProviderError("openrouter", "Invalid response structure")
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                logger.warning(
                    "openrouter_output_truncated",
                    model=selected_model,
                    max_tokens=max_tokens,
                )
            return str(choice.message.content).strip()
        except OpenAIRateLimitError as e:
            raise LLMRateLimitError("openrouter", "Rate limit exceeded", e) from e
        except OpenAIAPIError as e:
            raise LLMProviderError("openrouter", f"API request failed: {e}", e) from e
        except Exception as e:
            raise LLMProviderError("openrouter", f"Unexpected error: {e}", e) from e


class AnthropicProvider(LLMProvider):
    """Anthropic Claude messages provider (Async)."""

    _async_client: Optional[AsyncAnthropic] = None

    async def initialize_client(self) -> None:
        """Create the async Anthropic client if not yet initialised."""
        if self._async_client is None:
            logger.info("Initializing Anthropic client for {}", self.default_model)
            self._async_client = AsyncAnthropic(api_key=self.api_key)

    async def close(self) -> None:
        """Close Anthropic client session to free sockets."""
        if self._async_client:
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
        """Generate a Claude message completion."""
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        messages_payload = [{"role": "user", "content": prompt}]

        assert self._async_client is not None
        client = self._async_client

        try:
            response = await client.messages.create(
                model=selected_model,
                messages=cast(list[Any], messages_payload),
                system=system_prompt or "",  # Claude requires str
                temperature=actual_temperature,
                max_tokens=max_tokens if max_tokens is not None else 2048,
                **kwargs,
            )
            content = response.content[0].text if response.content else None
            if content is None:
                raise LLMProviderError("anthropic", "Invalid response structure")
            return str(content).strip()
        except AnthropicRateLimitError as e:
            raise LLMRateLimitError("anthropic", "Rate limit exceeded", e) from e
        except AnthropicAPIError as e:
            raise LLMProviderError("anthropic", f"API request failed: {e}", e) from e
        except Exception as e:
            raise LLMProviderError("anthropic", f"Unexpected error: {e}", e) from e


class GeminiProvider(LLMProvider):
    """Google Gemini text-generation provider using the SDK (Gemini Developer API)."""

    # Hold the GenAI async client wrapper (``client.aio``)
    _async_client: Optional[Any] = None

    async def initialize_client(self) -> None:
        """Lazy-create and cache an async GenAI client instance."""
        # (Re)initialise if no client cached OR cached client comes from deprecated SDK
        if self._async_client is None or not hasattr(self._async_client, "models"):
            from importlib.metadata import version as _pkg_version  # py3.8+

            try:
                genai_version = _pkg_version("google-genai")
            except Exception:
                genai_version = "unknown"

            logger.info(
                "Initializing Gemini client for {} (google-genai version={})",
                self.default_model,
                genai_version,
            )
            # The client automatically reads GEMINI_API_KEY but we also pass the explicit key
            _sync_client = genai.Client(api_key=self.api_key)
            # Expose the async facade provided by the SDK
            self._async_client = _sync_client.aio

    async def close(self) -> None:
        """Gemini SDK has no explicit close; drop cached client."""
        self._async_client = None  # sdk handles pooling internally
        await asyncio.sleep(0)

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate text with Gemini, honouring global temp/max settings."""
        await self.initialize_client()
        actual_temperature = self._get_effective_temperature(temperature)

        assert self._async_client is not None
        gm_client = self._async_client

        import inspect

        # ------------------------------------------
        # Build kwargs dynamically from signature
        # ------------------------------------------
        sig_params = set(
            inspect.signature(gm_client.models.generate_content).parameters
        )

        _req_kwargs: dict[str, Any] = {
            "model": (model or self.default_model),
            "contents": prompt,
        }

        # Pass optional kwargs ONLY if the target SDK version supports them.
        if "temperature" in sig_params and actual_temperature is not None:
            _req_kwargs["temperature"] = actual_temperature
        if "max_output_tokens" in sig_params and max_tokens is not None:
            _req_kwargs["max_output_tokens"] = max_tokens
        if "system_instruction" in sig_params and system_prompt:
            _req_kwargs["system_instruction"] = system_prompt

        logger.debug("Gemini generate_content final kwargs: {}", _req_kwargs)

        try:
            response = await gm_client.models.generate_content(**_req_kwargs)

            if not getattr(response, "text", ""):
                raise LLMProviderError("gemini", "Empty response text")
            return str(response.text).strip()
        except Exception as gen_exc:
            # Gracefully handle SDK signature churn or validation issues.
            err_msg = str(gen_exc)
            retriable = any(
                key in err_msg
                for key in ("generation_config", "temperature", "system_instruction")
            )

            if retriable:
                logger.warning(
                    "GeminiProvider primary call failed - retrying minimal signature",
                    error=err_msg,
                )

                # Retry with the absolute minimal supported signature to maximise cross-version compat.
                try:
                    response = await gm_client.models.generate_content(
                        model=(model or self.default_model),
                        contents=prompt,
                    )

                    if not getattr(response, "text", ""):
                        raise LLMProviderError(
                            "gemini", "Empty response text (fallback)"
                        )
                    return str(response.text).strip()
                except Exception as fallback_exc:  # noqa: BLE001 – surface below
                    # Chain the original error context for easier debugging.
                    raise LLMProviderError(
                        "gemini",
                        f"Fallback generate_content failed: {fallback_exc}",
                        fallback_exc,
                    ) from gen_exc
            # Non-retriable – propagate as provider error.
            raise


class XAIProvider(LLMProvider):
    """xAI Grok provider using the native xai-sdk."""

    _async_client: Optional[Any] = None

    async def initialize_client(self) -> None:
        """Lazily create and cache the xAI AsyncClient."""
        if self._async_client is None:
            from xai_sdk import AsyncClient as XAIAsyncClient

            logger.info("Initializing xAI client for {}", self.default_model)
            self._async_client = XAIAsyncClient(api_key=self.api_key)

    async def close(self) -> None:
        """Drop cached client reference."""
        self._async_client = None
        await asyncio.sleep(0)

    async def generate_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = 2048,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Generate text using xAI Grok."""
        await self.initialize_client()
        assert self._async_client is not None
        selected_model = model or self.default_model

        from xai_sdk.chat import system, user

        messages = []
        if system_prompt:
            messages.append(system(system_prompt))

        try:
            chat = self._async_client.chat.create(
                model=selected_model,
                messages=messages,
            )
            chat.append(user(prompt))
            response = await chat.sample()
            content = (
                response.content if hasattr(response, "content") else str(response)
            )
            if not content:
                raise LLMProviderError("xai", "Empty response from Grok")
            return str(content).strip()
        except Exception as e:
            err_msg = str(e).lower()
            if "rate limit" in err_msg or "429" in err_msg:
                raise LLMRateLimitError("xai", "Rate limit exceeded", e) from e
            raise LLMProviderError("xai", f"xAI API error: {e}", e) from e
