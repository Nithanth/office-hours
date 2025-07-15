"""Centralised application configuration loaded from environment variables."""

import datetime
import os
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

# Load .env only in development/local environments to avoid accidental leakage
if os.getenv("ENV", "dev") != "prod":  # Treat any non-"prod" value as dev
    load_dotenv()


# --- Nested Pydantic stubs (currently unused, reserved for future) ---
class GeneralConfig(BaseModel):
    app_name: str
    log_level: str


class OpenAIConfig(BaseModel):
    api_key: Optional[str] = None
    default_model: Optional[str] = None


class TogetherAIConfig(BaseModel):
    api_key: Optional[str] = None
    default_model: Optional[str] = None


class AnthropicConfig(BaseModel):
    api_key: Optional[str] = None
    default_model: Optional[str] = None


class GeminiConfig(BaseModel):
    api_key: Optional[str] = None
    default_model: Optional[str] = None


class LLMConfig(BaseModel):
    default_provider: str
    request_timeout: int
    temperature: float
    max_retries: int
    together_ai: Optional[TogetherAIConfig] = None
    openai: Optional[OpenAIConfig] = None
    anthropic: Optional[AnthropicConfig] = None
    gemini: Optional[GeminiConfig] = None


class Config:
    # API Keys
    EXA_API_KEY: Optional[str] = os.getenv("EXA_API_KEY")
    TOGETHER_API_KEY: Optional[str] = os.getenv("TOGETHER_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    GEMINI_API_KEY: Optional[str] = os.getenv(
        "GEMINI_API_KEY"
    )  # Google AI Studio API Key for Gemini
    # Path to your Google credentials JSON file - will be deferred for MVP
    GOOGLE_DOCS_CREDENTIALS_PATH: Optional[str] = os.getenv("GOOGLE_DOCS_CREDENTIALS_PATH")

    # --- Redis settings ---
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", 3600))  # 1 hour default

    # --- Modal settings ---
    MODAL_TOKEN: Optional[str] = os.getenv("MODAL_TOKEN")
    MODAL_WORKSPACE: Optional[str] = os.getenv("MODAL_WORKSPACE")
    MODAL_TOKEN_ID: Optional[str] = os.getenv("MODAL_TOKEN_ID")
    MODAL_TOKEN_SECRET: Optional[str] = os.getenv("MODAL_TOKEN_SECRET")

    # --- App runtime settings ---
    # --- Environment and asset prefixes ---
    ENV: str = os.getenv("ENV", "dev").lower()
    # Front-end CSS folder: use purged bundle in production for smaller payload.
    CSS_PREFIX: str = os.getenv("CSS_PREFIX", "css/dist" if ENV == "prod" else "css")

    APP_TITLE: str = "Lux Scholar"
    VERSION: str = "0.1.0"
    YEAR: int = int(os.getenv("YEAR", datetime.datetime.utcnow().year))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
    LOG_FORMAT_JSON: bool = os.getenv("LUX_LOG_FORMAT_JSON", "False").lower() == "true"
    DEFAULT_PAPER_COUNT: int = int(os.getenv("DEFAULT_PAPER_COUNT", 3))
    DEFAULT_SIMILAR_PAPER_COUNT: int = int(
        os.getenv("DEFAULT_SIMILAR_PAPER_COUNT", 3)
    )  # Added for similar paper searches
    RELOAD_APP: bool = os.getenv("RELOAD_APP", "True").lower() == "true"
    MAX_AUTHORS_DISPLAY: int = 5  # Max authors to display before 'et al.'

    # --- Paper/report tuning ---
    MAX_PAPERS_PER_SEARCH: int = 20
    SUMMARY_MAX_LENGTH: int = 300  # words
    MAX_CONCURRENT_PAPER_SUMMARIES: int = int(os.getenv("MAX_CONCURRENT_PAPER_SUMMARIES", 4))
    # Hard cap on characters passed from a paper into any LLM prompt (≈15k tokens).
    PAPER_MAX_CONTENT_SNIPPET_CHARS: int = int(os.getenv("PAPER_MAX_CONTENT_SNIPPET_CHARS", 60000))
    LLM_TIMEOUT_SECONDS: int = int(os.getenv("LLM_TIMEOUT_SECONDS", 120))
    # Optional UX pause when serving cached summaries – set to 0 to disable.
    CACHED_SUMMARY_DELAY_SECONDS: float = float(os.getenv("CACHED_SUMMARY_DELAY_SECONDS", "1.5"))

    QUALITATIVE_LENGTH_GUIDANCE: dict[str, str] = {
        "S": "brief and concise, summarizing only the absolute key takeaways",
        "M": "moderately detailed, covering the main points comprehensively but without excessive depth on any single one",
        "L": "detailed and thorough, exploring the nuances and providing substantial supporting information for each key aspect",
        # XL removed – we now standardise on S, M, L only.
    }

    SUMMARY_OUTPUT_TOKEN_TARGETS: dict[str, dict[str, int]] = {
        "technical": {
            "S": 2000,
            "M": 4000,
            "L": 6000,
        },
        "semi_technical": {
            "S": 2000,
            "M": 4000,
            "L": 6000,
        },
        "non_technical": {
            "S": 2000,
            "M": 4000,
            "L": 6000,
        },
    }

    # --- LLM request defaults ---
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", 0.7))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", 3))
    # Async semaphore limit per LLM provider to avoid QPS throttling.
    MAX_CONCURRENT_REQUESTS_PER_PROVIDER: int = int(
        os.getenv("MAX_CONCURRENT_REQUESTS_PER_PROVIDER", 4)
    )
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", 120))

    # Topic summary tuning
    MAX_TOPIC_SUMMARY_TOKENS: int = int(os.getenv("MAX_TOPIC_SUMMARY_TOKENS", 2048))

    # --- Chat prompt sizing ---
    CHAT_MAX_TOKENS: int = int(os.getenv("CHAT_MAX_TOKENS", 512))
    EXCERPT_CHARS: int = int(os.getenv("EXCERPT_CHARS", 2000))

    # --- Exa API back-off ---
    EXA_MAX_RETRIES: int = int(os.getenv("EXA_MAX_RETRIES", 3))
    EXA_RETRY_MIN_WAIT: int = int(os.getenv("EXA_RETRY_MIN_WAIT", 1))  # seconds
    EXA_RETRY_MAX_WAIT: int = int(os.getenv("EXA_RETRY_MAX_WAIT", 10))  # seconds

    # --- Default/fallback LLM models ---
    DEFAULT_MODEL_KEY: str = os.getenv("DEFAULT_MODEL_KEY", "gemini_2_5_pro_preview")

    AVAILABLE_MODELS: dict[str, dict[str, Any]] = {
        # Gemini (Google) - internal provider tag remains "gemini"
        "gemini_2_5_pro_preview": {
            "id": "models/gemini-2.5-pro-preview-06-05",
            "provider": "gemini",
            "input_token_limit": 1_000_000,
            "display_name": "Gemini 2.5 Pro",
        },
        "gemini_flash_2_5": {
            "id": "models/gemini-2.5-flash-preview-05-20",
            "provider": "gemini",
            "input_token_limit": 1_000_000,
            "display_name": "Gemini 2.5 Flash",
        },
        # Anthropic Claude
        "anthropic_claude_opus_4_preview": {
            "id": "claude-opus-4-20250514",
            "provider": "anthropic",
            "input_token_limit": 200_000,
            "display_name": "Claude Opus 4",
        },
        "anthropic_claude_sonnet_4": {
            "id": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "input_token_limit": 200_000,
            "display_name": "Claude Sonnet 4",
        },
        # OpenAI GPT
        "openai_gpt_4o": {
            "id": "gpt-4o",
            "provider": "openai",
            "input_token_limit": 128_000,
            "display_name": "GPT-4o",
        },
        "openai_gpt_4_1": {
            "id": "gpt-4.1",
            "provider": "openai",
            "input_token_limit": 128_000,
            "display_name": "GPT-4.1",
        },
        "openai_gpt_o3": {
            "id": "o3",
            "provider": "openai",
            "input_token_limit": 128_000,
            "display_name": "o3 (Coming Soon...)",
        },
        # TogetherAI Models
        "together_deepseek_v3": {
            "id": "deepseek-ai/DeepSeek-V3",
            "provider": "together_ai",
            "input_token_limit": 131_000,
            "display_name": "DeepSeek V3 (TogetherAI)",
        },
        "together_llama_4_maverick": {
            "id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "provider": "together_ai",
            "input_token_limit": 950_000,
            "display_name": "LLaMA-4 Maverick 17B (TogetherAI)",
        },
        "together_qwen3_235b": {
            "id": "Qwen/Qwen3-235B-A22B-fp8-tput",
            "provider": "together_ai",
            "input_token_limit": 128_000,
            "display_name": "Qwen3 235B (TogetherAI)",
        },
    }

    # Fallback TogetherAI model if primary fails
    TOGETHER_FALLBACK_MODEL: Optional[str] = "gpt-4o"

    __all__: list[str] = ["Config", "config"]


# Instantiate config for easy import
config = Config()
