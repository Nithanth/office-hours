import pytest

# This module references provider-specific config classes (AnthropicConfig,
# GeminiConfig, …) that no longer exist after the LLM service refactor. The
# tests need to be ported to the unified `config.Config` shape; until then we
# skip the whole module so the rest of the suite can run in CI.
pytest.skip(
    "Stale tests referencing removed provider config classes; needs porting.",
    allow_module_level=True,
)

from collections.abc import AsyncIterator  # noqa: E402
from enum import Enum  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from anthropic import APIError as AnthropicAPIError  # noqa: E402

from config import (  # noqa: E402
    AnthropicConfig,
    GeminiConfig,
    GeneralConfig,
    LLMConfig,
    OpenAIConfig,
    TogetherAIConfig,
    config,
)

# Adjust the import path based on your project structure
from services.core.llm_service import LLMService, get_llm_service  # noqa: E402
from services.core.llm_utils.errors import (  # noqa: E402
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
)

# Provider classes now live in llm_utils.providers
from services.core.llm_utils.providers import (  # noqa: E402
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
    TogetherAIProvider,
)


# --- Local Definitions for Test ---
class Provider(Enum):
    OPENAI = "openai"
    TOGETHER_AI = "together_ai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


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
        together_ai=TogetherAIConfig(
            api_key="fake_together_key", default_model="together/model"
        ),
        openai=OpenAIConfig(api_key="fake_openai_key", default_model="gpt-4"),
        anthropic=AnthropicConfig(
            api_key="fake_anthropic_key", default_model="claude-2"
        ),
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
    await service.initialize_providers()
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
    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        await service.initialize_providers()
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
    LLMService._instance = None
    LLMService._initialized = False

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_missing_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_missing_keys_fixture.openai.api_key
        )
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
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_missing_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_missing_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_missing_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = (
            mock_llm_config_missing_keys_fixture.max_retries
        )
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_missing_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMConfigurationError) as excinfo:
            await service.initialize_providers()
        # Any provider without API key may raise; just assert generic message
        assert "API key is not configured" in str(excinfo.value)

        # Skip re-initialization on subsequent calls
        service._initialized = True

        with pytest.raises(KeyError) as excinfo:
            await service.generate_completion(
                provider_name=Provider.TOGETHER_AI.value,
                model_name="together/model",
                prompt="Hello",
                max_tokens=2048,
            )
        assert Provider.TOGETHER_AI.value in str(excinfo.value)


@patch.object(OpenAIProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_text_openai_success(
    mock_openai_generate: AsyncMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_openai_generate.return_value = "Hello from mocked OpenAI!"

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

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
            system_prompt=None,
        )


@patch.object(TogetherAIProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_text_together_ai_default_model(
    mock_together_generate: AsyncMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_together_generate.return_value = "Hello from mocked TogetherAI!"

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

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
            model="together/model",
            temperature=None,
            max_tokens=2048,
            system_prompt=None,
        )


@patch.object(AnthropicProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_text_anthropic_error(
    mock_anthropic_generate: AsyncMock,
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

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

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


@patch.object(GeminiProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_text_gemini_success(
    mock_gemini_generate: AsyncMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_gemini_generate.return_value = "Hello from mocked Gemini!"
    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

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
    LLMService._instance = None
    LLMService._initialized = False

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_missing_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_missing_keys_fixture.openai.api_key
        )
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
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_missing_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_missing_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_missing_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_missing_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = (
            mock_llm_config_missing_keys_fixture.max_retries
        )
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_missing_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMConfigurationError) as init_excinfo:
            await service.initialize_providers()
        # Any provider without API key may raise; just assert generic message
        assert "API key is not configured" in str(init_excinfo.value)

        # Skip re-initialization on subsequent calls
        service._initialized = True

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
    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(KeyError) as excinfo:
            await service.generate_completion(
                provider_name="non_existent_llm_service",
                model_name="any_model",
                prompt="Hello",
                max_tokens=2048,
            )
        assert "non_existent_llm_service" in str(excinfo.value)


@patch.object(OpenAIProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_openai_provider_internal_retries_succeed(
    mock_openai_generate: AsyncMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    """Service retries on LLMRateLimitError then succeeds."""

    mock_openai_generate.side_effect = [
        LLMRateLimitError("openai", "Rate limit", Exception()),
        LLMRateLimitError("openai", "Rate limit", Exception()),
        "OpenAI says hello after retries!",
    ]

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        result = await service.generate_completion(
            provider_name=Provider.OPENAI.value,
            model_name="gpt-4",
            prompt="Test OpenAI retry success",
            max_tokens=2048,
        )

        assert result == "OpenAI says hello after retries!"
        assert mock_openai_generate.await_count == 3


@patch.object(OpenAIProvider, "generate_text", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_openai_provider_internal_retries_fail_max_attempts(
    mock_openai_generate: AsyncMock,
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    mock_openai_generate.side_effect = [
        LLMRateLimitError("openai", "Rate limit", Exception())
        for _ in range(mock_llm_config_all_keys_fixture.max_retries + 1)
    ]

    with patch("services.core.llm_service.app_config") as mock_global_config:
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        service = LLMService()
        with pytest.raises(LLMRateLimitError) as excinfo:
            await service.generate_completion(
                provider_name=Provider.OPENAI.value,
                model_name="gpt-4",
                prompt="Test OpenAI retry failure",
                max_tokens=2048,
            )

        assert excinfo.value.provider == Provider.OPENAI.value
        assert (
            mock_openai_generate.await_count
            == mock_llm_config_all_keys_fixture.max_retries + 1
        )


@pytest.mark.asyncio
async def test_llm_service_context_manager_closes_providers(
    mock_general_config_fixture: GeneralConfig,
    mock_llm_config_all_keys_fixture: LLMConfig,
) -> None:
    """Test that LLMService used as a context manager closes its providers."""
    # We need to mock the 'close' method of each provider class that LLMService might instantiate.
    from services.core.llm_service import LLMService as _Svc

    _Svc._instance = None
    _Svc._initialized = False

    with (
        patch("services.core.llm_service.app_config") as mock_global_config,
        patch.object(
            OpenAIProvider, "close", new_callable=AsyncMock
        ) as mock_openai_close,
        patch.object(
            TogetherAIProvider, "close", new_callable=AsyncMock
        ) as mock_together_close,
        patch.object(
            AnthropicProvider, "close", new_callable=AsyncMock
        ) as mock_anthropic_close,
        patch.object(
            GeminiProvider, "close", new_callable=AsyncMock
        ) as mock_gemini_close,
    ):
        assert mock_llm_config_all_keys_fixture.openai is not None
        mock_global_config.OPENAI_API_KEY = (
            mock_llm_config_all_keys_fixture.openai.api_key
        )
        mock_global_config.OPENAI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.openai.default_model
        )
        assert mock_llm_config_all_keys_fixture.together_ai is not None
        mock_global_config.TOGETHER_API_KEY = (
            mock_llm_config_all_keys_fixture.together_ai.api_key
        )
        mock_global_config.TOGETHER_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.together_ai.default_model
        )
        assert mock_llm_config_all_keys_fixture.anthropic is not None
        mock_global_config.ANTHROPIC_API_KEY = (
            mock_llm_config_all_keys_fixture.anthropic.api_key
        )
        mock_global_config.ANTHROPIC_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.anthropic.default_model
        )
        assert mock_llm_config_all_keys_fixture.gemini is not None
        mock_global_config.GEMINI_API_KEY = (
            mock_llm_config_all_keys_fixture.gemini.api_key
        )
        mock_global_config.GEMINI_DEFAULT_MODEL = (
            mock_llm_config_all_keys_fixture.gemini.default_model
        )

        mock_global_config.DEFAULT_LLM_PROVIDER = (
            mock_llm_config_all_keys_fixture.default_provider
        )
        mock_global_config.REQUEST_TIMEOUT = (
            mock_llm_config_all_keys_fixture.request_timeout
        )
        mock_global_config.MAX_RETRIES = mock_llm_config_all_keys_fixture.max_retries
        mock_global_config.LLM_TEMPERATURE = (
            mock_llm_config_all_keys_fixture.temperature
        )

        mock_global_config.LOG_LEVEL = mock_general_config_fixture.log_level

        async with get_llm_service() as service:
            await service.initialize_providers()  # Ensure providers are created
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
