from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, NoReturn
from unittest.mock import MagicMock, patch

import httpx
import pytest
from anthropic import APIError as AnthropicAPIError  # Added for testing

from config import (
    AnthropicConfig,
    GeminiConfig,
    GeneralConfig,
    LLMConfig,
    OpenAIConfig,
    TogetherAIConfig,
    config,
)

# Adjust the import path based on your project structure
from services.llm_client import (
    AnthropicProvider,
    GeminiProvider,
    LLMConfigurationError,
    LLMError,
    LLMProviderError,
    LLMService,
    OpenAIProvider,
    TogetherAIProvider,
    get_llm_service,
)


# --- Local Definitions for Test ---
class Provider(Enum):
    OPENAI = "openai"
    TOGETHER_AI = "together_ai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class UnsupportedProviderError(LLMError):
    """Raised when an unsupported LLM provider is requested."""

    pass


# Basic configurations for testing
@pytest.fixture
def mock_general_config_fixture() -> GeneralConfig:
    return GeneralConfig(app_name="TestApp", log_level="DEBUG")


@pytest.fixture
def mock_llm_config_all_keys_fixture() -> LLMConfig:
    return LLMConfig(
        default_provider="openai",
        request_timeout=config.REQUEST_TIMEOUT,
        temperature=config.LLM_TEMPERATURE,
        max_retries=config.MAX_RETRIES,
        together_ai=TogetherAIConfig(api_key="fake_together_key", default_model="together/model"),
        openai=OpenAIConfig(api_key="fake_openai_key", default_model="gpt-4"),
        anthropic=AnthropicConfig(api_key="fake_anthropic_key", default_model="claude-2"),
        gemini=GeminiConfig(api_key="fake_gemini_key", default_model="gemini-pro"),
    )


@pytest.fixture
def mock_llm_config_missing_keys_fixture() -> LLMConfig:
    # Simulates a config where some API keys might be missing
    return LLMConfig(
        default_provider="openai",
        request_timeout=config.REQUEST_TIMEOUT,
        temperature=config.LLM_TEMPERATURE,
        max_retries=config.MAX_RETRIES,
        # Missing TogetherAI key
        together_ai=TogetherAIConfig(default_model="together/model"),
        openai=OpenAIConfig(api_key="fake_openai_key", default_model="gpt-4"),
        # Missing Anthropic key
        anthropic=AnthropicConfig(default_model="claude-2"),
        # Missing Gemini key
        gemini=GeminiConfig(default_model="gemini-pro"),
    )


@pytest.fixture
async def llm_service_all_keys_fixture() -> AsyncIterator[LLMService]:
    """Fixture for LLMService with all API keys, initialized and cleaned up."""
    service = LLMService()
    await service.initialize_all_providers()
    yield service
    await service.close_all_providers()


@pytest.fixture
async def llm_service_missing_keys_fixture() -> AsyncIterator[LLMService]:
    """Fixture for LLMService where some API keys are missing."""
    service = LLMService()
    yield service
    await service.close_all_providers()


# --- Test Cases ---


@pytest.mark.asyncio
async def test_llm_service_initialization_all_keys(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        await service.initialize_all_providers()
        assert service.providers is not None
        assert service.logger is not None
        assert (
            mock_llm_config_all_keys_fixture.default_provider
            == mock_global_config.DEFAULT_LLM_PROVIDER
        )
        assert Provider.OPENAI.value in service.providers
        assert Provider.TOGETHER_AI.value in service.providers
        assert Provider.ANTHROPIC.value in service.providers
        assert Provider.GEMINI.value in service.providers
        assert isinstance(service.providers[Provider.OPENAI.value], OpenAIProvider)


@pytest.mark.asyncio
async def test_llm_service_initialization_missing_key_raises_error(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_missing_keys_fixture: LLMConfig,
) -> None:
    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_missing_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_missing_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.openai.default_model
        )
        assert mock_llm_config_missing_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_missing_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_missing_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_missing_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_missing_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_missing_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_missing_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_missing_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_missing_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_missing_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMConfigurationError) as excinfo:
            await service.initialize_all_providers()
        assert "OpenAIProvider API key is not configured" in str(excinfo.value)
        await service.close_all_providers()


@patch.object(OpenAIProvider, "generate_text", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_generate_text_openai_success(
    mock_openai_generate: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_openai_generate.return_value = "Hello from mocked OpenAI!"

    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        result = await service.generate_completion(
            provider_name=Provider.OPENAI.value,
            model_name="gpt-custom",
            prompt="Hello OpenAI",
            temperature=0.5,
            max_tokens=50,
        )

        assert result == "Hello from mocked OpenAI!"
        mock_openai_generate.assert_awaited_once_with(
            prompt="Hello OpenAI",
            model="gpt-custom",
            temperature=0.5,
            max_tokens=50,
            system_prompt="Be an assistant.",
        )


@patch.object(TogetherAIProvider, "generate_text", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_generate_text_together_ai_default_model(
    mock_together_generate: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_together_generate.return_value = "Hello from mocked TogetherAI!"

    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        result = await service.generate_completion(
            provider_name=Provider.TOGETHER_AI.value,
            model_name="together/model",
            prompt="Hello TogetherAI",
            max_tokens=2048,
        )

        assert result == "Hello from mocked TogetherAI!"
        mock_together_generate.assert_awaited_once_with(
            prompt="Hello TogetherAI",
            model=None,
            temperature=None,
            max_tokens=None,
            system_prompt=None,
        )


@patch.object(AnthropicProvider, "generate_text", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_generate_text_anthropic_error(
    mock_anthropic_generate: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    original_error = AnthropicAPIError(
        message="fake anthropic error",
        request=MagicMock(),
        body={},  # Added to satisfy type checker stub
    )
    mock_anthropic_generate.side_effect = LLMProviderError(
        provider=Provider.ANTHROPIC.value,
        message="Anthropic API call failed",
        original_exception=original_error,
    )

    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMProviderError) as excinfo:
            await service.generate_completion(
                provider_name=Provider.ANTHROPIC.value,
                model_name="claude-2",
                prompt="Hello Anthropic",
                max_tokens=2048,
            )

        assert excinfo.value.provider == Provider.ANTHROPIC.value
        assert "Anthropic API call failed" in str(excinfo.value)
        assert excinfo.value.original_exception == original_error
        mock_anthropic_generate.assert_awaited_once()


@patch.object(GeminiProvider, "generate_text", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_generate_text_gemini_success(
    mock_gemini_generate: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_gemini_generate.return_value = "Hello from mocked Gemini!"
    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        result = await service.generate_completion(
            provider_name=Provider.GEMINI.value,
            model_name="gemini-pro",
            prompt="Hello Gemini",
            max_tokens=2048,
        )
        assert result == "Hello from mocked Gemini!"
        mock_gemini_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_text_uninitialized_provider_key_missing(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_missing_keys_fixture: LLMConfig,
) -> None:
    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_missing_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_missing_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.openai.default_model
        )
        assert mock_llm_config_missing_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_missing_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_missing_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_missing_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_missing_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_missing_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_missing_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_missing_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_missing_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_missing_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMConfigurationError) as init_excinfo:
            await service.initialize_all_providers()
        assert (
            "TogetherAIProvider API key is not configured" in str(init_excinfo.value)
            or "AnthropicProvider API key is not configured" in str(init_excinfo.value)
            or "GeminiProvider API key is not configured" in str(init_excinfo.value)
        )

        with pytest.raises(KeyError) as excinfo:
            await service.generate_completion(
                provider_name=Provider.TOGETHER_AI.value,
                model_name="together/model",
                prompt="Hello",
                max_tokens=2048,
            )
        assert Provider.TOGETHER_AI.value in str(excinfo.value)


@pytest.mark.asyncio
async def test_generate_text_unsupported_provider_string(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(KeyError) as excinfo:
            await service.generate_completion(
                provider_name="non_existent_llm_service",  # type: ignore[arg-type]
                model_name="any_model",
                prompt="Hello",
                max_tokens=2048,
            )
        assert "non_existent_llm_service" in str(excinfo.value)


@patch.object(OpenAIProvider, "_async_client", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_openai_provider_internal_retries_succeed(
    mock_openai_async_client_instance: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    """Test that OpenAIProvider (called by LLMService) retries on transient errors and succeeds."""
    mock_post_method = MagicMock()
    mock_openai_async_client_instance.post = mock_post_method

    mock_transient_error_response = MagicMock(spec=httpx.Response)
    mock_transient_error_response.status_code = 503
    mock_transient_error_response.request = MagicMock(url="http://fakeurl.com/api/openai")
    mock_transient_error_response.text = "OpenAI service temporarily unavailable"

    def raise_http_status_error(*args: Any, **kwargs: Any) -> NoReturn:
        raise httpx.HTTPStatusError(
            message="OpenAI service temporarily unavailable",
            request=mock_transient_error_response.request,
            response=mock_transient_error_response,
        )

    mock_successful_response_json = {
        "choices": [{"message": {"content": "OpenAI says hello after retries!"}}],
        "usage": {"total_tokens": 20},
    }
    mock_successful_httpx_response = MagicMock(spec=httpx.Response)
    mock_successful_httpx_response.status_code = 200
    mock_successful_httpx_response.json.return_value = mock_successful_response_json

    mock_post_method.side_effect = [
        httpx.RequestError("OpenAI connection failed first time"),
        MagicMock(side_effect=raise_http_status_error),
        mock_successful_httpx_response,
    ]

    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        actual_openai_provider = service.providers[Provider.OPENAI.value]
        with patch.object(
            actual_openai_provider, "_async_client", new_callable=MagicMock
        ) as mock_provider_client_instance:
            mock_provider_client_instance.post.side_effect = [
                httpx.RequestError("OpenAI connection failed first time"),
                MagicMock(side_effect=raise_http_status_error),
                mock_successful_httpx_response,
            ]

            result = await service.generate_completion(
                provider_name=Provider.OPENAI.value,
                model_name="gpt-4",
                prompt="Test OpenAI retry success",
                max_tokens=2048,
            )

            assert result == "OpenAI says hello after retries!"
            assert mock_provider_client_instance.post.call_count == 3


@patch.object(OpenAIProvider, "_async_client", new_callable=MagicMock)
@pytest.mark.asyncio
async def test_openai_provider_internal_retries_fail_max_attempts(
    mock_openai_async_client_instance: MagicMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    """Test OpenAIProvider fails after max retries if errors persist."""
    mock_post_method = MagicMock()
    mock_openai_async_client_instance.post = mock_post_method
    mock_post_method.side_effect = httpx.RequestError("OpenAI connection failed repeatedly")

    with patch("services.llm_client.config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        actual_openai_provider = service.providers[Provider.OPENAI.value]
        with patch.object(
            actual_openai_provider, "_async_client", new_callable=MagicMock
        ) as mock_provider_client_instance:
            mock_provider_client_instance.post.side_effect = httpx.RequestError(
                "OpenAI connection failed repeatedly"
            )

            with pytest.raises(LLMProviderError) as excinfo:
                await service.generate_completion(
                    provider_name=Provider.OPENAI.value,
                    model_name="gpt-4",
                    prompt="Test OpenAI retry failure",
                    max_tokens=2048,
                )

            assert "Request failed: OpenAI connection failed repeatedly" in str(excinfo.value)
            assert excinfo.value.provider == Provider.OPENAI.value
            assert (
                mock_provider_client_instance.post.call_count
                == mock_llm_config_all_keys_fixture.max_retries
            )


@pytest.mark.asyncio
async def test_llm_service_context_manager_closes_providers(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    """Test that LLMService used as a context manager closes its providers."""
    # We need to mock the 'close' method of each provider class that LLMService might instantiate.
    with (
        patch("services.llm_client.config") as mock_global_config,
        patch.object(OpenAIProvider, "close", new_callable=MagicMock) as mock_openai_close,
        patch.object(TogetherAIProvider, "close", new_callable=MagicMock) as mock_together_close,
        patch.object(AnthropicProvider, "close", new_callable=MagicMock) as mock_anthropic_close,
        patch.object(GeminiProvider, "close", new_callable=MagicMock) as mock_gemini_close,
    ):
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = mock_llm_config_all_keys_fixture.openai.api_key
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = mock_llm_config_all_keys_fixture.together_ai.api_key
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = mock_llm_config_all_keys_fixture.anthropic.api_key
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = mock_llm_config_all_keys_fixture.gemini.api_key
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = mock_llm_config_all_keys_fixture.default_provider
        mock_global_config.REQUEST_TIMEOUT = mock_llm_config_all_keys_fixture.request_timeout
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = mock_llm_config_all_keys_fixture.temperature

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        async with get_llm_service() as service:
            await service.initialize_all_providers()  # Ensure providers are created
            assert Provider.OPENAI.value in service.providers  # Quick check
            # No actual call to generate_text needed, just testing setup and teardown

        # Assert that close was called on the providers that would have been initialized
        if mock_global_config.OPENAI_API_KEY:
            mock_openai_close.assert_awaited_once()
        if mock_global_config.TOGETHER_API_KEY:
            mock_together_close.assert_awaited_once()
        if mock_global_config.ANTHROPIC_API_KEY:
            mock_anthropic_close.assert_awaited_once()
        if mock_global_config.GEMINI_API_KEY:
            mock_gemini_close.assert_awaited_once()
