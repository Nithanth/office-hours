"""LLM provider orchestration layer with unified async routing and structured logging."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from inspect import isawaitable
from typing import Any, Optional, cast

from loguru import logger

import services.core.llm_utils.providers as llm_providers
from config import config as app_config
from services.core.llm_utils.errors import (
    LLMConfigurationError,
    LLMRateLimitError,
)
from services.core.llm_utils.provider_base import LLMProvider
from services.core.llm_utils.types import LLMResult, ProviderName
from utils.observability import _METRICS_ENABLED, LLM_TOKENS


class LLMService:
    """Orchestrates LLM calls to various providers."""

    _instance = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "LLMService":
        if cls._instance is None:
            cls._instance = super().__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self) -> None:
        # Guard on instance attribute so re-creating the singleton works in tests.
        if getattr(self, "_initialized", False):
            return
        self._initialized = False

        # Bind component field for richer context in every log entry
        self.logger = logger.bind(component="LLMService")
        self.providers: dict[ProviderName, LLMProvider] = {}

        # Ad-hoc OpenRouter providers for request-scoped BYOK keys,
        # keyed by a hash of the user's API key.
        self._byok_providers: dict[str, LLMProvider] = {}

        # Per-provider concurrency guard (semaphore)
        concurrency_limit_raw = getattr(
            app_config, "MAX_CONCURRENT_REQUESTS_PER_PROVIDER", 4
        )
        concurrency_limit: int = (
            concurrency_limit_raw
            if isinstance(concurrency_limit_raw, int) and concurrency_limit_raw > 0
            else 4
        )

        self._semaphores: dict[ProviderName, asyncio.Semaphore] = {
            name: asyncio.Semaphore(concurrency_limit)
            for name in (
                "together_ai",
                "openai",
                "anthropic",
                "gemini",
                "xai",
                "openrouter",
            )
        }

        # ---------------- Provider configuration ----------------
        self.provider_configs: dict[ProviderName, dict[str, Any]] = {
            "openai": {
                "api_key": app_config.OPENAI_API_KEY,
                "default_model": self._resolve_default_model("openai"),
                "ProviderClass": llm_providers.OpenAIProvider,
            },
            "together_ai": {
                "api_key": app_config.TOGETHER_API_KEY,
                "default_model": self._resolve_default_model("together_ai"),
                "ProviderClass": llm_providers.TogetherAIProvider,
            },
            "anthropic": {
                "api_key": app_config.ANTHROPIC_API_KEY,
                "default_model": self._resolve_default_model("anthropic"),
                "ProviderClass": llm_providers.AnthropicProvider,
            },
            "gemini": {
                "api_key": app_config.GEMINI_API_KEY,
                "default_model": self._resolve_default_model("gemini"),
                "ProviderClass": llm_providers.GeminiProvider,
            },
            "xai": {
                "api_key": app_config.XAI_API_KEY,
                "default_model": self._resolve_default_model("xai"),
                "ProviderClass": llm_providers.XAIProvider,
            },
            "openrouter": {
                "api_key": app_config.OPENROUTER_API_KEY,
                "default_model": self._resolve_default_model("openrouter"),
                "ProviderClass": llm_providers.OpenRouterProvider,
            },
        }

        for _pname, _pconf in self.provider_configs.items():
            api_key_val = _pconf["api_key"]
            default_model_val = _pconf.get("default_model")
            # Only pre-create provider instances when *both* API key and default model exist.
            if not api_key_val or not default_model_val:
                continue

            # Ignore any instantiation errors at this early stage – they will
            # surface later during full async initialisation when we have
            # better logging context.
            with suppress(Exception):
                self.providers[_pname] = _pconf["ProviderClass"](
                    api_key=api_key_val,
                    default_model=str(default_model_val),
                )

    async def initialize_providers(self) -> None:
        """Initializes all configured LLM providers asynchronously."""
        if self._initialized:
            return

        self.logger.info("initializing providers")
        initialized_any = False

        for provider_name, config in self.provider_configs.items():
            api_key = config.get("api_key")
            ProviderClass = config["ProviderClass"]
            default_model_id = config["default_model"]

            # ---- Graceful validation ----
            if not api_key or not isinstance(api_key, str) or not api_key.strip():
                self.logger.warning(
                    "provider skipped - missing API key",
                    provider=ProviderClass.__name__,
                )
                # Ensure the provider is not left in the pool from a previous run
                self.providers.pop(provider_name, None)
                continue

            if not default_model_id:
                self.logger.warning(
                    "provider skipped - missing default model",
                    provider=ProviderClass.__name__,
                )
                self.providers.pop(provider_name, None)
                continue

            try:
                provider_instance = self.providers.get(provider_name) or ProviderClass(
                    api_key=api_key, default_model=default_model_id
                )
                # Ensure async client ready
                await provider_instance.initialize_client()
                self.providers[provider_name] = provider_instance
                initialized_any = True
                self.logger.info(
                    "provider initialized",
                    provider=ProviderClass.__name__,
                    model=default_model_id,
                )
            except Exception as e:
                self.logger.error(
                    "provider init failed",
                    provider=ProviderClass.__name__,
                    error=str(e),
                    exc_info=True,
                )
                self.providers.pop(provider_name, None)

        if not initialized_any:
            raise LLMConfigurationError(
                "No LLM providers could be initialised; configure at least one API key."
            )

        self._initialized = True
        self.logger.info("LLMService initialization complete.")

    def is_provider_initialized(self, provider_name: ProviderName) -> bool:
        """Checks if the specified LLM provider is initialized and available."""
        is_init = provider_name in self.providers
        if not is_init:
            self.logger.warning(
                "provider not initialized",
                provider=provider_name,
            )
        return is_init

    async def close_all_providers(self) -> None:
        """Closes all initialized provider clients."""
        logger.info("Closing all LLM provider clients.")
        for provider_name, provider in self.providers.items():
            try:
                close_result = provider.close()
                if isawaitable(close_result):
                    await close_result
                logger.debug("provider closed", provider=provider_name)
            except Exception as e:
                self.logger.error(
                    "provider close error",
                    provider=provider_name,
                    error=str(e),
                    exc_info=True,
                )
        self._initialized = False
        self.providers.clear()

    def _resolve_provider(self, provider_name: ProviderName) -> LLMProvider | None:
        """Return the provider instance for a request.

        For ``openrouter``, a request-scoped BYOK key (set via the
        model-prefs context) takes precedence over the shared instance.
        """
        if provider_name == "openrouter":
            from services.core import model_prefs
            from services.core.llm_utils.providers import OpenRouterProvider

            byok_key = model_prefs.current_openrouter_api_key()
            if byok_key and byok_key.strip():
                import hashlib

                key_hash = hashlib.sha256(byok_key.encode()).hexdigest()[:24]
                provider = self._byok_providers.get(key_hash)
                if provider is None:
                    provider = OpenRouterProvider(
                        api_key=byok_key,
                        default_model=self._resolve_default_model("openrouter")
                        or "google/gemini-3.5-flash",
                    )
                    self._byok_providers[key_hash] = provider
                return provider
        return self.providers.get(provider_name)

    async def generate_completion_stream(
        self,
        provider_name: ProviderName,
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

        provider = self._resolve_provider(provider_name)
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
                from openai import (
                    AsyncOpenAI,
                )  # Local import to avoid hard dep when key missing
                from openai.types.chat import ChatCompletionMessageParam

                openai_client = cast(AsyncOpenAI, provider._async_client)
                if openai_client is None:
                    return _fallback_stream()
                # Build messages
                msgs: list[ChatCompletionMessageParam] = []
                if system_prompt:
                    msgs.append({"role": "system", "content": system_prompt})
                msgs.append({"role": "user", "content": prompt})

                effective_temp: float = temperature if temperature is not None else 0.7

                async def _openai_stream() -> AsyncIterator[str]:
                    try:
                        resp = await openai_client.chat.completions.create(
                            model=model_name,
                            messages=msgs,
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
            except Exception as exc:
                self.logger.warning("openai_stream_setup_failed", err=str(exc))
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
                        effective_temp: float = (
                            temperature if temperature is not None else 0.7
                        )
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
                            if hasattr(event, "delta") and getattr(
                                event.delta, "text", None
                            ):
                                yield str(event.delta.text)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "Anthropic streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _anthropic_stream()
            except Exception as exc:
                self.logger.warning("anthropic_stream_setup_failed", err=str(exc))
                return _fallback_stream()

        # Together AI native streaming
        if provider_name == "together_ai":
            try:
                from together import AsyncTogether

                tg_client = cast(AsyncTogether, provider._async_client)
                if tg_client is None:
                    tg_client = AsyncTogether(api_key=app_config.TOGETHER_API_KEY)
                    provider._async_client = tg_client

                async def _together_stream() -> AsyncIterator[str]:
                    try:
                        msgs: list[dict[str, str]] = []
                        if system_prompt:
                            msgs.append({"role": "system", "content": system_prompt})
                        msgs.append({"role": "user", "content": prompt})

                        eff_temp: float = (
                            temperature if temperature is not None else 0.7
                        )

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
            except Exception as exc:
                self.logger.warning("together_stream_setup_failed", err=str(exc))
                return _fallback_stream()

        # Gemini native streaming
        if provider_name == "gemini":
            try:
                from google import genai

                gm_client = cast(Any, provider._async_client)
                if gm_client is None:
                    gm_client = genai.Client(api_key=app_config.GEMINI_API_KEY)
                    provider._async_client = gm_client

                async def _gemini_stream() -> AsyncIterator[str]:
                    try:
                        final_prompt = (
                            f"System Prompt: {system_prompt}\n\n---\n\n{prompt}"
                            if system_prompt
                            else prompt
                        )

                        # gm_client is already an _AsyncClient instance (see GeminiProvider)
                        # so we can call generate_content_stream directly.
                        stream_iter = cast(
                            AsyncIterator[Any],
                            await gm_client.models.generate_content_stream(
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
            except Exception as exc:
                self.logger.warning("gemini_stream_setup_failed", err=str(exc))
                return _fallback_stream()

        # xAI native streaming
        if provider_name == "xai":
            try:
                from xai_sdk.chat import system as xai_system
                from xai_sdk.chat import user as xai_user

                xai_client = provider._async_client
                if xai_client is None:
                    return _fallback_stream()

                async def _xai_stream() -> AsyncIterator[str]:
                    try:
                        messages = []
                        if system_prompt:
                            messages.append(xai_system(system_prompt))

                        chat = xai_client.chat.create(
                            model=model_name,
                            messages=messages,
                        )
                        chat.append(xai_user(prompt))
                        for _response, chunk in await chat.stream():
                            if hasattr(chunk, "content") and chunk.content:
                                yield str(chunk.content)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.exception(
                            "xAI streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _xai_stream()
            except Exception as exc:
                self.logger.warning("xai_stream_setup_failed", err=str(exc))
                return _fallback_stream()

        # OpenRouter native streaming (OpenAI-compatible)
        if provider_name == "openrouter":
            try:
                from openai import AsyncOpenAI
                from openai.types.chat import ChatCompletionMessageParam

                or_client = cast(AsyncOpenAI, provider._async_client)
                if or_client is None:
                    return _fallback_stream()

                openrouter_msgs: list[ChatCompletionMessageParam] = []
                if system_prompt:
                    openrouter_msgs.append({"role": "system", "content": system_prompt})
                openrouter_msgs.append({"role": "user", "content": prompt})

                openrouter_effective_temp: float = (
                    temperature if temperature is not None else 0.7
                )

                async def _openrouter_stream() -> AsyncIterator[str]:
                    try:
                        resp = await or_client.chat.completions.create(
                            model=model_name,
                            messages=openrouter_msgs,
                            max_tokens=max_tokens,
                            temperature=openrouter_effective_temp,
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
                            "OpenRouter streaming failed - falling back", exc_info=exc
                        )
                        async for tok in _fallback_stream():
                            yield tok

                return _openrouter_stream()
            except Exception as exc:
                self.logger.warning("openrouter_stream_setup_failed", err=str(exc))
                return _fallback_stream()

        # Default fallback – should rarely be reached if all provider branches handled.
        return _fallback_stream()

    async def generate_completion(
        self,
        provider_name: ProviderName,
        model_name: str,
        prompt: str,
        max_tokens: int,
        stop_sequences: Optional[list[str]] | None = None,
        temperature: Optional[float] | None = None,
        system_prompt: Optional[str] | None = None,
        *,
        request_id: Optional[str] = None,
    ) -> str:
        """Return a full completion string.

        This wrapper delegates to the chosen ``LLMProvider`` and catches
        common error cases, turning them into plain error strings so that
        upstream callers never need ``try/except`` around user-visible
        completions.
        """
        if not self._initialized:
            await self.initialize_providers()  # Ensure initialized

        provider = self._resolve_provider(provider_name)
        if not provider:
            self.logger.error("provider missing", provider=provider_name)
            raise KeyError(provider_name)
        if provider_name == "openrouter":
            await provider.initialize_client()

        # Retry with exponential back-off on rate-limit errors.
        retries_remaining = app_config.MAX_RETRIES
        backoff_seconds = 1.0

        req_prefix = f"[req:{request_id}] " if request_id else ""

        while True:
            semaphore = self._semaphores[provider_name]
            async with semaphore:
                try:
                    generated = provider.generate_text(
                        prompt=prompt,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        system_prompt=system_prompt,
                    )

                    if isawaitable(generated):
                        generated = await generated

                    return cast(str, generated)
                except LLMRateLimitError as rl_err:
                    if retries_remaining > 0:
                        self.logger.warning(
                            "rate-limit retry",
                            provider=provider_name,
                            wait=backoff_seconds,
                            retries_left=retries_remaining,
                            request=req_prefix.strip() or None,
                        )
                        await asyncio.sleep(backoff_seconds)
                        retries_remaining -= 1
                        backoff_seconds = min(backoff_seconds * 2, 20.0)
                        continue
                    else:
                        self.logger.error(
                            "rate-limit exhausted",
                            provider=provider_name,
                            request=req_prefix.strip() or None,
                        )
                        raise rl_err
                except Exception as exc:
                    # Non-retryable or unexpected – bubble up to caller block.
                    raise exc

    async def generate_completion_safe(
        self,
        provider_name: ProviderName,
        model_name: str,
        prompt: str,
        max_tokens: int,
        stop_sequences: Optional[list[str]] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None,
        *,
        request_id: Optional[str] = None,
    ) -> LLMResult:
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
                request_id=request_id,
            )
            is_ok = not content.startswith("Error:")
            if is_ok and _METRICS_ENABLED and LLM_TOKENS is not None:
                # Rough token estimate – whitespace split is sufficient for counters.
                LLM_TOKENS.inc(len(content.split()))
            return {"ok": is_ok, "content": content}
        except Exception as e:  # catch any leakage
            # Emit full stack trace for observability before surfacing generic error to UI.
            self.logger.exception("llm_generate_completion_safe_error", exc_info=e)
            return {"ok": False, "content": f"Unhandled exception in LLM call: {e}"}

    def _resolve_default_model(self, provider: ProviderName) -> str | None:
        """Return the default model id for *provider*.

        Precedence:
        1. Provider-specific override on ``app_config`` (e.g. ``OPENAI_DEFAULT_MODEL``).
        2. ``app_config.DEFAULT_MODEL_KEY`` entry in :pydata:`AVAILABLE_MODELS`.
        3. The first matching model in :pydata:`AVAILABLE_MODELS`.

        Returns ``None`` when no match is found; the caller is expected to
        raise :class:`LLMConfigurationError` during validation.
        """

        override_map = {
            "openai": "OPENAI_DEFAULT_MODEL",
            "together_ai": "TOGETHER_DEFAULT_MODEL",
            "anthropic": "ANTHROPIC_DEFAULT_MODEL",
            "gemini": "GEMINI_DEFAULT_MODEL",
            "xai": "XAI_DEFAULT_MODEL",
            "openrouter": "OPENROUTER_DEFAULT_MODEL",
        }

        override_attr = override_map.get(provider)
        if override_attr:
            override_val = getattr(app_config, override_attr, None)
            if isinstance(override_val, str) and override_val.strip():
                return override_val.strip()

        # Config-wide default
        default_info = app_config.AVAILABLE_MODELS.get(app_config.DEFAULT_MODEL_KEY)
        if default_info and default_info["provider"] == provider:
            return str(default_info["id"])

        # Fallback – first entry matching provider
        for info in app_config.AVAILABLE_MODELS.values():
            if info["provider"] == provider:
                return str(info["id"])

        # Still nothing → warn; validation later will error.
        self.logger.warning(
            "no default model configured",
            provider=provider,
            override_attr=override_attr,
        )
        return None


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
        logger.debug("closing providers (context manager)")
        await service.close_all_providers()
