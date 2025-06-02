import httpx
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Literal, AsyncIterator, List

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError

# Attempt relative import for normal package use
try:
    from ..config import config as app_config
except ImportError:
    # Fallback for direct script execution (e.g., python -m services.llm_client)
    import sys
    import os
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if PROJECT_ROOT not in sys.path:
        sys.path.append(PROJECT_ROOT)
    from config import config as app_config

# Import official client libraries
from openai import AsyncOpenAI, APIError as OpenAIAPIError, RateLimitError as OpenAIRateLimitError
from anthropic import AsyncAnthropic, APIError as AnthropicAPIError, RateLimitError as AnthropicRateLimitError
import google.generativeai as genai
from google.generativeai.types import GenerationConfig # For typing
from google.api_core.exceptions import GoogleAPIError, ResourceExhausted as GeminiRateLimitError
from together import Together

# Setup basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=app_config.LOG_LEVEL.upper(), format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

LLMProviderName = Literal["together_ai", "openai", "anthropic", "gemini"]

# --- Custom Exceptions ---
class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass

class LLMProviderError(LLMError):
    """Raised when there's a specific error with an LLM provider's API call."""
    def __init__(self, provider: str, message: str, original_exception: Optional[Exception] = None):
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
    def __init__(self, provider: str, message: str, is_safety_issue: bool = False):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.is_safety_issue = is_safety_issue

# --- LLM Provider Abstract Base Class ---
class LLMProvider(ABC):
    """Abstract base class for an LLM provider."""

    def __init__(self, api_key: Optional[str], default_model: str):
        if not api_key:
            raise LLMConfigurationError(f"{self.__class__.__name__} API key is not configured or missing.")
        self.api_key = api_key
        self.default_model = default_model
        self._async_client: Optional[Any] = None # To be defined by subclasses

    @abstractmethod
    async def initialize_client(self):
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
        **kwargs: Any
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
    async def close(self):
        """Cleans up any resources, like HTTP clients."""
        pass

    def _get_effective_temperature(self, temperature: Optional[float]) -> float:
        return temperature if temperature is not None else app_config.LLM_TEMPERATURE

# --- Concrete LLM Provider Implementations ---

class TogetherAIProvider(LLMProvider):
    """LLM Provider for Together AI using the official Together Python client."""

    async def initialize_client(self):
        if self._async_client is None:
            logger.debug(f"Initializing TogetherAI client for {self.default_model}")
            # The Together client is synchronous, so we'll initialize it once
            # and use it in async functions with asyncio.to_thread
            self._async_client = Together(api_key=self.api_key)

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=3, max=20),
        retry=retry_if_exception_type(LLMRateLimitError),
        reraise=True
    )
    async def _attempt_generation_with_model(self, prompt: str, model_to_use: str, temperature: Optional[float] = None, max_tokens: Optional[int] = 2048, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        await self.initialize_client()
        actual_temperature = self._get_effective_temperature(temperature)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.debug(f"TogetherAI request: Model={model_to_use}, Temp={actual_temperature}, MaxTokens={max_tokens}")

        try:
            # Use asyncio.to_thread to run the synchronous Together client in a separate thread
            response = await asyncio.to_thread(
                self._async_client.chat.completions.create,
                model=model_to_use,
                messages=messages,
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            
            # Extract the response text
            if not response.choices or not response.choices[0].message or not response.choices[0].message.content:
                raise LLMProviderError("together_ai", f"Invalid response structure from API for model {model_to_use}.", None)
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            # Handle different types of exceptions from the Together client
            if "rate limit" in str(e).lower() or "429" in str(e):
                logger.warning(f"TogetherAI rate limit hit for model {model_to_use}.")
                raise LLMRateLimitError("together_ai", f"Rate limit exceeded for model {model_to_use}.", e) from e
            
            # Log the error with detailed information
            logger.error(f"TogetherAI error for model {model_to_use}: {type(e).__name__}: {str(e)}")
            
            # Create a user-friendly error message
            error_message = f"API request failed for model {model_to_use}: {str(e)}"
            raise LLMProviderError("together_ai", error_message, e) from e

    async def generate_text(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = 2048, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        primary_model_to_use = model or self.default_model
        try:
            logger.info(f"Attempting TogetherAI generation with primary model: {primary_model_to_use}")
            return await self._attempt_generation_with_model(
                prompt, primary_model_to_use, temperature, max_tokens, system_prompt, **kwargs
            )
        except (LLMProviderError, RetryError) as e_primary: # RetryError wraps the last attempt's LLMProviderError
            logger.warning(f"Primary TogetherAI model {primary_model_to_use} failed: {e_primary}. Attempting fallback.")
            fallback_model_to_use = app_config.TOGETHER_FALLBACK_MODEL
            if not fallback_model_to_use:
                logger.error(f"No fallback model configured for TogetherAI. Primary failure: {e_primary}")
                raise e_primary # Re-raise the original error if no fallback
            if fallback_model_to_use == primary_model_to_use:
                logger.warning(f"Fallback model {fallback_model_to_use} is the same as primary. Not re-attempting.")
                raise e_primary # Re-raise the original error
            
            try:
                logger.info(f"Attempting TogetherAI generation with fallback model: {fallback_model_to_use}")
                return await self._attempt_generation_with_model(
                    prompt, fallback_model_to_use, temperature, max_tokens, system_prompt, **kwargs
                )
            except (LLMProviderError, RetryError) as e_fallback:
                logger.error(f"Fallback TogetherAI model {fallback_model_to_use} also failed: {e_fallback}. Both primary and fallback attempts exhausted.")
                raise e_fallback # Raise the fallback error as the final one


    async def close(self):
        if self._async_client:
            logger.info("Dereferencing TogetherAI client. No explicit close needed for synchronous client.")
            self._async_client = None
        else:
            logger.debug("TogetherAI client was None, no action to close.")

class OpenAIProvider(LLMProvider):
    """LLM Provider for OpenAI."""
    async def initialize_client(self):
        if self._async_client is None:
            logger.debug(f"Initializing OpenAI client for {self.default_model}")
            self._async_client = AsyncOpenAI(api_key=self.api_key, timeout=app_config.REQUEST_TIMEOUT, max_retries=0) # Retries handled by tenacity

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((OpenAIAPIError, LLMRateLimitError)), # Catch generic APIError for retries too
        reraise=True
    )
    async def generate_text(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = 2048, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.debug(f"OpenAI request: Model={selected_model}, Temp={actual_temperature}, MaxTokens={max_tokens}")
        try:
            response = await self._async_client.chat.completions.create( # type: ignore
                model=selected_model,
                messages=messages, # type: ignore
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            if not response.choices or not response.choices[0].message or response.choices[0].message.content is None:
                 raise LLMProviderError("openai", "Invalid response structure from API.", None)
            return response.choices[0].message.content.strip()
        except OpenAIRateLimitError as e:
            logger.warning(f"OpenAI rate limit hit for model {selected_model}.")
            raise LLMRateLimitError("openai", "Rate limit exceeded.", e) from e
        except OpenAIAPIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise LLMProviderError("openai", f"API request failed: {e}", e) from e
        except Exception as e: # Catch any other unexpected errors
            logger.error(f"Unexpected error with OpenAI provider: {e}")
            raise LLMProviderError("openai", f"An unexpected error occurred: {e}", e) from e

    async def close(self):
        if self._async_client:
            logger.debug("Closing OpenAI client.")
            # AsyncOpenAI client might not have an explicit close, or it's handled by __aexit__
            # For now, we'll just nullify it. If issues arise, consult OpenAI SDK docs.
            await self._async_client.close() # Added based on httpx client underlying AsyncOpenAI
            self._async_client = None

class AnthropicProvider(LLMProvider):
    """LLM Provider for Anthropic."""
    async def initialize_client(self):
        if self._async_client is None:
            logger.debug(f"Initializing Anthropic client for {self.default_model}")
            self._async_client = AsyncAnthropic(api_key=self.api_key, timeout=app_config.REQUEST_TIMEOUT, max_retries=0) # Retries by tenacity

    @retry(
        stop=stop_after_attempt(app_config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((AnthropicAPIError, LLMRateLimitError)), # Catch generic APIError
        reraise=True
    )
    async def generate_text(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = 2048, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        await self.initialize_client()
        selected_model = model or self.default_model
        actual_temperature = self._get_effective_temperature(temperature)

        # Anthropic API expects system prompt as a top-level parameter
        messages = [{"role": "user", "content": prompt}]

        logger.debug(f"Anthropic request: Model={selected_model}, Temp={actual_temperature}, MaxTokens={max_tokens}")
        try:
            response = await self._async_client.messages.create( # type: ignore
                model=selected_model,
                messages=messages,
                system=system_prompt, # Pass system prompt here
                temperature=actual_temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            if not response.content or not response.content[0].text:
                raise LLMProviderError("anthropic", "Invalid response structure from API.", None)
            return response.content[0].text.strip()
        except AnthropicRateLimitError as e:
            logger.warning(f"Anthropic rate limit hit for model {selected_model}.")
            raise LLMRateLimitError("anthropic", "Rate limit exceeded.", e) from e
        except AnthropicAPIError as e:
            logger.error(f"Anthropic API error: {e}")
            raise LLMProviderError("anthropic", f"API request failed: {e}", e) from e
        except Exception as e:
            logger.error(f"Unexpected error with Anthropic provider: {e}")
            raise LLMProviderError("anthropic", f"An unexpected error occurred: {e}", e) from e

    async def close(self):
        if self._async_client:
            logger.debug("Closing Anthropic client.")
            await self._async_client.close() # Added based on httpx client underlying AsyncAnthropic
            self._async_client = None

class GeminiProvider(LLMProvider):
    """LLM Provider for Google Gemini."""
    async def initialize_client(self):
        if self._async_client is None:
            logger.debug(f"Initializing Gemini client for {self.default_model} (API key: {'set' if self.api_key else 'not set'})")
            if not self.api_key:
                 raise LLMConfigurationError("Gemini API key is not configured.")
            try:
                # API key configuration is global for this library
                genai.configure(api_key=self.api_key)
                # The client is the model object itself
                self._async_client = genai.GenerativeModel(self.default_model)
            except Exception as e:
                logger.error(f"Failed to configure Gemini API or initialize model: {e}")
                raise LLMConfigurationError(f"Gemini client initialization failed: {e}", e) from e

    async def generate_text(self, prompt: str, model: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = 2048, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        await self.initialize_client()
        # For Gemini, if model changes, client might need re-initialization or use a different model object.
        # For simplicity, we assume default_model is used or model passed is compatible with initial client.
        # If you need to switch models frequently with Gemini, a different client management strategy might be needed.
        selected_model_name = model or self.default_model
        if model and model != self.default_model:
            logger.warning(f"Gemini provider was initialized with {self.default_model}, but {model} was requested. Attempting to use {model}.")
            # Re-initialize client for the new model if it's different from the one used for initialization
            # This is a simplified approach; robust model switching might require a pool of model clients.
            try:
                current_client_model = self._async_client # type: ignore
                if hasattr(current_client_model, 'model_name') and current_client_model.model_name != selected_model_name:
                    logger.info(f"Re-initializing Gemini client for model: {selected_model_name}")
                    self._async_client = genai.GenerativeModel(selected_model_name)
            except Exception as e:
                raise LLMConfigurationError(f"Failed to switch Gemini model to {selected_model_name}: {e}", e)

        actual_temperature = self._get_effective_temperature(temperature)
        
        generation_config_args = {"temperature": actual_temperature}
        if max_tokens is not None:
            generation_config_args["max_output_tokens"] = max_tokens
        
        # System prompt handling for Gemini (prepended to user prompt or via specific API features if available)
        # The genai library's `generate_content` takes a list of 'Content' parts or a simple string.
        # For system prompts, it's common to either prepend it or use multi-turn chat structure if supported directly.
        # Let's use a simple list of contents for now.
        contents: List[Any] = []
        if system_prompt:
            # Gemini's API can take structured content. System instructions can be part of the history.
            # Or, for simpler cases, prepend to the user prompt if no explicit system role is used in the model call.
            # For a more robust solution, explore `genai.ChatSession` for multi-turn conversations.
            # For now, we'll just pass it as part of the prompt or a separate 'system_instruction' if the model supports it.
            # The `GenerativeModel.generate_content` can accept `system_instruction` for some models.
            # We'll add it to kwargs if not directly supported by a top-level param here.
            if 'system_instruction' not in kwargs: # Check if user passed it via kwargs
                 kwargs['system_instruction'] = genai.types.Content(parts=[genai.types.Part(text=system_prompt)], role="system")

        contents.append(prompt) # User prompt

        logger.debug(f"Gemini request: Model={selected_model_name}, Temp={actual_temperature}, MaxTokens={max_tokens}")
        try:
            # The client is the model object itself
            gemini_model_client = self._async_client
            raw_async_call_result = gemini_model_client.generate_content_async( # type: ignore
                contents=contents,
                generation_config=genai.types.GenerationConfig(**generation_config_args), # Explicitly using genai.types
                **kwargs
            )
            logger.info(f"Type of raw_async_call_result: {type(raw_async_call_result)}")
            logger.info(f"Is it awaitable? {hasattr(raw_async_call_result, '__await__')}")
            
            response = await raw_async_call_result

            if not response.candidates:
                logger.error("Gemini response contained no candidates.")
                raise LLMResponseError("gemini", "Response contained no candidates.")

            candidate = response.candidates[0]
            # Ensure finish_reason is accessed safely and converted to its string name
            finish_reason_value = candidate.finish_reason
            finish_reason_name = "OTHER" # Default
            if hasattr(finish_reason_value, 'name'): # Check if it's an enum with a .name attribute
                finish_reason_name = finish_reason_value.name
            elif finish_reason_value: # If it's a value (e.g., int), try to map it (simplified)
                # For a more robust mapping, you might need a dictionary if direct .name isn't available
                # For now, we'll rely on the value itself if .name is not present, or keep 'OTHER'
                # This part might need refinement based on actual type of finish_reason_value if not an enum
                # For example, if it's an int: {1: "STOP", 2: "MAX_TOKENS", ...}.get(finish_reason_value, "OTHER")
                # However, the SDK usually provides an enum with .name.
                logger.warning(f"finish_reason (value: {finish_reason_value}, type: {type(finish_reason_value)}) does not have a .name attribute. Using value directly or 'OTHER'.")
                # Attempt to convert to string if it's a simple type like int
                try:
                    finish_reason_name = str(finish_reason_value) 
                    # A more specific mapping for known integer codes could be added here if necessary
                    # e.g. if finish_reason_value == 1: finish_reason_name = "STOP"
                except Exception:
                    logger.error(f"Could not convert finish_reason_value to string.")
            
            logger.info(f"Gemini API call completed. Finish Reason: {finish_reason_name}")

            text_parts = []
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text: # Ensure part.text exists and is not empty
                        text_parts.append(part.text)
            
            full_text = "".join(text_parts)

            if not full_text.strip(): # If no text or only whitespace
                if finish_reason_name not in ["STOP", "UNSPECIFIED", "MAX_TOKENS"]:
                    error_message = (
                        f"Gemini generation resulted in no text content. "
                        f"Finish Reason: {finish_reason_name}. "
                        f"Max Output Tokens: {generation_config_args.get('max_output_tokens')}."
                    )
                    if finish_reason_name == "SAFETY":
                        safety_ratings_info = str(candidate.safety_ratings)
                        error_message += f" Safety Ratings: {safety_ratings_info}."
                        logger.warning(f"Gemini content blocked due to safety settings. Ratings: {safety_ratings_info}")
                        raise LLMResponseError("gemini", error_message, is_safety_issue=True)
                    
                    logger.error(error_message)
                    raise LLMResponseError("gemini", error_message)
                else:
                    logger.info(f"Gemini generated an empty response with finish_reason: {finish_reason_name}. Returning empty string.")
                    return "" 

            return full_text.strip()

        except GeminiRateLimitError as e:
            logger.warning(f"Gemini rate limit hit for model {selected_model_name}.")
            raise LLMRateLimitError("gemini", "Rate limit exceeded.", e) from e
        except GoogleAPIError as e:
            logger.error(f"Gemini API error: {e}")
            raise LLMProviderError("gemini", f"API request failed: {e}", e) from e
        except Exception as e:
            logger.error(f"Unexpected error with Gemini provider: {e}")
            raise LLMProviderError("gemini", f"An unexpected error occurred: {e}", e) from e

    async def close(self):
        # For Gemini, client is the model. No explicit close needed for the genai library typically.
        logger.debug("Gemini client does not require explicit close.")
        self._async_client = None

# --- LLM Service Orchestrator ---
class LLMService:
    """Orchestrates LLM calls to various providers."""
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(LLMService, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'providers'):
            self.providers: Dict[LLMProviderName, LLMProvider] = {}
            self.logger = logging.getLogger(__name__)
            self._initialized = False # Ensure initialized is False until explicitly set

    def _get_default_model_for_provider(self, provider_key: str) -> str:
        """Helper to find a default model ID for a given provider from AVAILABLE_MODELS."""
        # Attempt to find the model designated by DEFAULT_MODEL_KEY if it's for this provider
        default_model_info = app_config.AVAILABLE_MODELS.get(app_config.DEFAULT_MODEL_KEY)
        if default_model_info and default_model_info.get("provider") == provider_key:
            return default_model_info["id"]
        
        # Fallback: return the ID of the first model found for this provider
        for _model_friendly_name, details in app_config.AVAILABLE_MODELS.items():
            if details.get("provider") == provider_key:
                return details["id"]
        
        self.logger.error(f"LLMService._get_default_model_for_provider: No models found for provider '{provider_key}' in AVAILABLE_MODELS. Configuration might be incomplete.")
        # Return a placeholder or raise an error. For now, a placeholder that's unlikely to be a real model ID.
        return f"error_no_model_found_for_{provider_key}"

    async def initialize_all_providers(self):
        if self._initialized:
            self.logger.info("LLM providers already initialized.")
            return

        self.logger.info("Initializing all LLM providers...")
        provider_configs: Dict[str, Tuple[Type[LLMProvider], Optional[str], Optional[str]]] = {
            # Provider Key: (ProviderClass, API_KEY, DEFAULT_MODEL_ID_FOR_PROVIDER_INIT)
            "together_ai": (TogetherAIProvider, app_config.TOGETHER_API_KEY, self._get_default_model_for_provider("together_ai")),
            "openai": (OpenAIProvider, app_config.OPENAI_API_KEY, self._get_default_model_for_provider("openai")),
            "anthropic": (AnthropicProvider, app_config.ANTHROPIC_API_KEY, self._get_default_model_for_provider("anthropic")),
            "gemini": (GeminiProvider, app_config.GEMINI_API_KEY, self._get_default_model_for_provider("gemini")),
        }

        for provider_name, (ProviderClass, api_key, default_model_id) in provider_configs.items():
            if api_key:
                try:
                    # Pass the resolved default_model_id to the provider's constructor
                    self.providers[provider_name] = ProviderClass(api_key=api_key, default_model=default_model_id)
                    self.logger.info(f"{ProviderClass.__name__} initialized with model '{default_model_id}'.")
                except Exception as e:
                    self.logger.error(f"Failed to initialize {ProviderClass.__name__}: {e}")
            else:
                self.logger.warning(f"API key for {provider_name} not found. {ProviderClass.__name__} will not be available.")
        
        self._initialized = True
        self.logger.info("LLMService initialization complete.")

    async def close_all_providers(self):
        """Closes all initialized provider clients."""
        logger.info("Closing all LLM provider clients.")
        for provider_name, provider in self.providers.items():
            try:
                await provider.close()
                logger.debug(f"Successfully closed {provider_name} provider.")
            except Exception as e:
                logger.error(f"Error closing provider {provider_name}: {e}", exc_info=True)
        self._initialized = False
        self.providers.clear()

    async def generate_completion(self, 
                                provider_name: LLMProviderName, 
                                model_name: str, 
                                prompt: str, 
                                max_tokens: int,
                                stop_sequences: Optional[List[str]] = None,
                                temperature: Optional[float] = None
                                ) -> str:
        if not self._initialized:
            await self.initialize_all_providers() # Ensure initialized

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
                prompt,
                model=model_name, # If None, provider uses its default
                temperature=temperature, # If None, provider uses global or its default
                max_tokens=max_tokens,
                system_prompt=None, # Not used in this method
                **{}
            )
        except RetryError as e: # Raised by tenacity after max retries
            logger.error(f"LLM call failed after multiple retries for provider {provider.__class__.__name__}: {e}")
            return f"LLM call failed after max retries with {provider.__class__.__name__}. Error: {e.last_attempt.exception()}" # Example of returning error string
        except LLMError as e: # Catch our custom LLM errors
            logger.error(f"LLM Error with {provider.__class__.__name__}: {e}")
            return f"Error with {provider.__class__.__name__}: {e}" # Example of returning error string
        except Exception as e:
            logger.error(f"Unexpected critical error during LLM call with {provider.__class__.__name__}: {e}", exc_info=True)
            return f"Critical error with {provider.__class__.__name__}. Check logs." # Example

# Singleton accessor function
def get_llm_service() -> LLMService:
    return LLMService()

# --- Context Manager for LLMService ---
from contextlib import asynccontextmanager

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
        await service.initialize_all_providers()
    try:
        yield service
    finally:
        logger.debug("get_llm_service: Closing providers within context manager.")
        await service.close_all_providers()

# --- Example Usage (for direct testing of this module) ---
async def main_test():
    print("Running LLM Service example...")
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.info(f"Configured default provider from env: {app_config.DEFAULT_LLM_PROVIDER}")
    logger.info(f"OpenAI Key set: {'Yes' if app_config.OPENAI_API_KEY else 'No'}")
    logger.info(f"Together Key set: {'Yes' if app_config.TOGETHER_API_KEY else 'No'}")
    logger.info(f"Anthropic Key set: {'Yes' if app_config.ANTHROPIC_API_KEY else 'No'}")
    logger.info(f"Gemini Key set: {'Yes' if app_config.GEMINI_API_KEY else 'No'}")

    sample_prompt = "Explain the concept of a Large Language Model in one sentence."
    system_prompt_example = "You are a helpful assistant that explains complex topics concisely."

    async with get_llm_service() as llm_service:
        # llm_service should be initialized by the context manager now
        if not llm_service.providers:
            logger.error("No LLM providers are configured with API keys. Exiting example.")
            return

        logger.info(f"\n--- Testing with Default Provider ({app_config.DEFAULT_LLM_PROVIDER} or fallback) ---")
        result_default = await llm_service.generate_completion(app_config.DEFAULT_LLM_PROVIDER, app_config.DEFAULT_MODEL_KEY, sample_prompt, 2048)
        if result_default:
            logger.info(f"Default Provider Result: {result_default}")
        else:
            logger.warning("Default provider call returned no result or an error.")

        # Test specific providers if configured
        test_providers: List[LLMProviderName] = ["openai", "together_ai", "anthropic", "gemini"]
        for provider_name_to_test in test_providers:
            if provider_name_to_test in llm_service.providers:
                logger.info(f"\n--- Testing with {provider_name_to_test.upper()} Provider ---")
                # Example of overriding model for TogetherAI
                model_override = None
                if provider_name_to_test == "together_ai":
                    model_override = "mistralai/Mixtral-8x7B-Instruct-v0.1" # A different model for testing
                
                result_specific = await llm_service.generate_completion(
                    provider_name_to_test, 
                    model_override, 
                    sample_prompt, 
                    2048, 
                    temperature=0.7, 
                    stop_sequences=["\n\n", "\n"]
                )
                if result_specific:
                    logger.info(f"{provider_name_to_test.upper()} Result: {result_specific}")
                else:
                    logger.warning(f"{provider_name_to_test.upper()} call returned no result or an error.")
            else:
                logger.info(f"\n--- {provider_name_to_test.upper()} Provider not configured, skipping test. ---")

if __name__ == "__main__":
    # To run this example: python -m services.llm_client # or python -m tests.integration.llm_client
    asyncio.run(main_test())