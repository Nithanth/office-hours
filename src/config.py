"""Centralised application configuration with Pydantic validation.

All settings are loaded from environment variables (with sensible defaults).
The ``config`` singleton is the primary interface used throughout the codebase.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env only in development/local environments to avoid accidental leakage
if os.getenv("ENV", "dev") != "prod":
    load_dotenv()


# ---------------------------------------------------------------------------
# Model info schema (used in AVAILABLE_MODELS)
# ---------------------------------------------------------------------------

_VALID_PROVIDERS = {"openai", "together_ai", "anthropic", "gemini", "xai", "openrouter"}


class Config(BaseSettings):
    """Application settings validated via Pydantic.

    Environment variables are mapped directly to field names (case-insensitive).
    All existing behaviour is preserved — fields default to the same values as
    the prior plain-class implementation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Don't re-load .env here; we already did it above conditionally.
        env_ignore_empty=True,
    )

    # ------------------------------------------------------------------
    # API Keys
    # ------------------------------------------------------------------
    EXA_API_KEY: Optional[str] = None
    TOGETHER_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    XAI_API_KEY: Optional[str] = None
    OPENROUTER_API_KEY: Optional[
        str
    ] = None  # Primary LLM gateway - all model tiers route through OpenRouter
    SEMANTIC_SCHOLAR_API_KEY: Optional[str] = None  # optional, raises rate limits
    # OpenAlex is the keyless fallback for author search; these are optional.
    # Free instant keys: https://help.openalex.org/api/authentication/
    OPENALEX_API_KEY: Optional[str] = None
    OPENALEX_MAILTO: Optional[str] = None  # polite-pool contact email

    # ------------------------------------------------------------------
    # Notion export
    # ------------------------------------------------------------------
    NOTION_API_KEY: Optional[str] = None
    NOTION_PARENT_PAGE_ID: Optional[str] = None
    NOTION_PARENT_DATABASE_ID: Optional[str] = None

    # ------------------------------------------------------------------
    # App runtime settings
    # ------------------------------------------------------------------
    ENV: str = "dev"
    CSS_PREFIX: str = ""
    APP_TITLE: str = "Office Hours"
    VERSION: str = "0.1.0"
    YEAR: int = Field(default_factory=lambda: datetime.datetime.now(datetime.UTC).year)
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "info"
    LOG_FORMAT_JSON: bool = Field(
        default=False,
        validation_alias=AliasChoices("OH_LOG_FORMAT_JSON"),
    )

    # ------------------------------------------------------------------
    # Auth & database
    # ------------------------------------------------------------------
    DATABASE_URL: Optional[str] = None
    AUTH_REDIRECT_BASE_URL: str = ""
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    NOTION_OAUTH_CLIENT_ID: Optional[str] = None
    NOTION_OAUTH_CLIENT_SECRET: Optional[str] = None
    SECRET_ENCRYPTION_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OH_SECRET_ENCRYPTION_KEY"),
    )
    REQUIRE_LOGIN: bool = False
    BETA_DAILY_REPORT_LIMIT: int = 20
    BETA_DAILY_SEARCH_LIMIT: int = 100

    # ------------------------------------------------------------------
    # Cache directories
    # ------------------------------------------------------------------
    CACHE_DIR: Path = Path(".cache")
    VECTOR_CACHE_DIR: Path = Path("")  # computed in validator
    SUMMARY_CACHE_DIR: Path = Path("")  # computed in validator

    # ------------------------------------------------------------------
    # Embedding / Vector store
    # ------------------------------------------------------------------
    EMB_FLOAT16: bool = False
    EMB_BATCH_SIZE: int = Field(default=32, ge=1)
    DISK_QUERY_CACHE: bool = False

    # ------------------------------------------------------------------
    # PDF download
    # ------------------------------------------------------------------
    USE_COND_FETCH: bool = False

    # ------------------------------------------------------------------
    # Chat behaviour
    # ------------------------------------------------------------------
    CHAT_MAX_TOKENS: int = Field(default=2048, ge=1)
    CHAT_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    CHAT_STREAM_BUFFER: int = Field(default=100, ge=1)
    CHAT_KEEPALIVE_SEC: int = Field(default=1, ge=0)

    # ------------------------------------------------------------------
    # Server tuning
    # ------------------------------------------------------------------
    UVICORN_WORKERS: Optional[int] = None
    RELOAD_APP: bool = True

    # ------------------------------------------------------------------
    # Search & display
    # ------------------------------------------------------------------
    DEFAULT_PAPER_COUNT: int = Field(default=60, ge=1)
    DEFAULT_SIMILAR_PAPER_COUNT: int = Field(default=3, ge=1)
    MAX_AUTHORS_DISPLAY: int = 5
    TEXT_PREWARM_CONCURRENCY: int = Field(default=8, ge=1)

    # ------------------------------------------------------------------
    # Logging & observability
    # ------------------------------------------------------------------
    LOG_FILE: Optional[str] = None
    ENABLE_METRICS: bool = False

    # ------------------------------------------------------------------
    # Vector store & RAG fine-tuning
    # ------------------------------------------------------------------
    RAG_SEARCH_MULTIPLIER: int = Field(default=25, ge=1)
    CE_RERANK_POOL: int = Field(default=40, ge=1)
    ENABLE_CE_RERANK: bool = False
    EXA_POOL_SIZE: int = Field(default=35, ge=1)
    # Exa search type: "auto" (fast, balanced), "neural" (semantic),
    # "keyword", or "deep" (higher quality, slower, costs more credits)
    EXA_SEARCH_TYPE: str = "auto"
    # Use highlights for token-efficient excerpts (reduces content tokens ~10x)
    EXA_USE_HIGHLIGHTS: bool = True

    # ------------------------------------------------------------------
    # PDF/Text processing
    # ------------------------------------------------------------------
    STRIP_REFERENCES: bool = False
    PAPER_CACHE_DB: Optional[str] = None
    PROMPT_OVERRIDE_DIR: Path = Path("")

    # ------------------------------------------------------------------
    # Admin & session security
    # ------------------------------------------------------------------
    ADMIN_API_KEY: Optional[str] = None
    SESSION_SECRET: str = Field(
        default="dev-insecure-secret",
        validation_alias=AliasChoices("OH_SESSION_SECRET"),
    )

    # ------------------------------------------------------------------
    # Chat state persistence
    # ------------------------------------------------------------------
    # Backend for chat state: "file" (default) or "redis"
    CHAT_STORE_BACKEND: str = "file"
    # Redis URL for the redis backend (ignored when using file backend)
    REDIS_URL: Optional[str] = None

    # ------------------------------------------------------------------
    # Paper/report tuning
    # ------------------------------------------------------------------
    MAX_PAPERS_PER_SEARCH: int = 20
    SUMMARY_MAX_LENGTH: int = 300
    MAX_CONCURRENT_PAPER_SUMMARIES: int = Field(default=4, ge=1)
    PAPER_MAX_CONTENT_SNIPPET_CHARS: int = Field(default=150000, ge=1000)
    LLM_TIMEOUT_SECONDS: int = Field(default=120, ge=1)
    CACHED_SUMMARY_DELAY_SECONDS: float = Field(default=1.5, ge=0.0)
    PDF_EXTRACTION_CONCURRENCY: int = Field(default=6, ge=1)

    # ------------------------------------------------------------------
    # PDF extraction
    # ------------------------------------------------------------------
    MAX_PDF_MB: int = Field(default=100, ge=1)
    MIN_FULL_TEXT_CHARS: int = Field(default=3000, ge=100)
    MIN_PARTIAL_TEXT_CHARS: int = Field(default=400, ge=50)
    ENABLE_PDF_OCR: bool = False
    OCR_MAX_PDF_MB: int = Field(default=10, ge=1)

    # ------------------------------------------------------------------
    # Length guidance (static mappings)
    # ------------------------------------------------------------------
    QUALITATIVE_LENGTH_GUIDANCE: dict[str, str] = {
        "S": "brief and concise (≈ 300 words), summarizing only the absolute key takeaways",
        "M": "moderately detailed (≈ 600 words), covering the main points comprehensively but without excessive depth on any single one",
        "L": "detailed and thorough (≈ 900 words), exploring nuances and providing substantial supporting information for each key aspect",
    }

    # Generous caps: reasoning models on the OpenRouter tiers spend hidden
    # thinking tokens against max_tokens, so tight targets truncate the
    # visible answer mid-sentence (finish_reason="length").
    SUMMARY_OUTPUT_TOKEN_TARGETS: dict[str, dict[str, int]] = {
        "technical": {"S": 4000, "M": 6000, "L": 9000},
        "semi_technical": {"S": 4000, "M": 6000, "L": 9000},
        "non_technical": {"S": 4000, "M": 6000, "L": 9000},
    }

    # ------------------------------------------------------------------
    # Hybrid summarisation
    # ------------------------------------------------------------------
    HYBRID_FULL_CONTEXT_CHAR_THRESHOLD: int = Field(default=50000, ge=1000)
    HYBRID_RAG_TOPK: int = Field(default=3, ge=1)

    # ------------------------------------------------------------------
    # LLM request defaults
    # ------------------------------------------------------------------
    LLM_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    MAX_RETRIES: int = Field(default=3, ge=0)
    MAX_CONCURRENT_REQUESTS_PER_PROVIDER: int = Field(default=4, ge=1)
    REQUEST_TIMEOUT: int = Field(default=120, ge=1)

    # ------------------------------------------------------------------
    # Feature toggles
    # ------------------------------------------------------------------
    ENABLE_URL_SIMILAR_OVERVIEW: bool = False
    URL_THEME_MIN_PAPERS: int = Field(default=3, ge=1)
    # Clamp for the topic-overview call. Reasoning tiers spend hidden
    # thinking tokens against max_tokens — 2048 truncated the Overview
    # mid-word even after the size targets were raised.
    MAX_TOPIC_SUMMARY_TOKENS: int = Field(default=9000, ge=1)

    # ------------------------------------------------------------------
    # Chat prompt sizing
    # ------------------------------------------------------------------
    EXCERPT_CHARS: int = 15000

    # ------------------------------------------------------------------
    # Exa API back-off
    # ------------------------------------------------------------------
    EXA_MAX_RETRIES: int = Field(default=3, ge=0)
    EXA_RETRY_MIN_WAIT: int = Field(default=1, ge=0)
    EXA_RETRY_MAX_WAIT: int = Field(default=10, ge=1)

    # ------------------------------------------------------------------
    # LLM model tiers (all served through OpenRouter)
    # ------------------------------------------------------------------
    DEFAULT_MODEL_KEY: str = "standard"
    TOGETHER_FALLBACK_MODEL: Optional[str] = "deepseek-ai/DeepSeek-V3"

    # Optional overrides for the pinned OpenRouter model IDs below
    # (dev/admin escape hatch - model IDs move fast).
    OPENROUTER_MODEL_FAST: Optional[str] = None
    OPENROUTER_MODEL_STANDARD: Optional[str] = None
    OPENROUTER_MODEL_DEEP: Optional[str] = None

    # Models offered in the per-user tier picker (OpenRouter author/slug IDs).
    # Users can also enter any catalog ID; this list is just the curated set.
    CURATED_OPENROUTER_MODELS: list[dict[str, str]] = [
        {"id": "google/gemini-3.5-flash", "label": "Gemini 3.5 Flash"},
        {"id": "anthropic/claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
        {"id": "google/gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro"},
        {"id": "anthropic/claude-opus-4.8", "label": "Claude Opus 4.8"},
        {"id": "openai/gpt-5.5", "label": "GPT-5.5"},
        {"id": "openai/gpt-5.5-pro", "label": "GPT-5.5 Pro"},
        {"id": "z-ai/glm-5.2", "label": "GLM 5.2 (Z.ai)"},
        {"id": "deepseek/deepseek-v3.2", "label": "DeepSeek v3.2"},
    ]

    AVAILABLE_MODELS: dict[str, dict[str, Any]] = {
        # Three task tiers, all routed through OpenRouter. Tier names are
        # what the UI shows; the model IDs are pinned here and can be
        # overridden per-tier via OPENROUTER_MODEL_{FAST,STANDARD,DEEP}.
        "fast": {
            "id": "google/gemini-3.5-flash",
            "provider": "openrouter",
            "input_token_limit": 1_000_000,
            "display_name": "Fast · Gemini 3.5 Flash",
        },
        "standard": {
            "id": "anthropic/claude-sonnet-4.6",
            "provider": "openrouter",
            "input_token_limit": 200_000,
            "display_name": "Standard · Claude Sonnet 4.6",
        },
        "deep": {
            "id": "google/gemini-3.1-pro-preview",
            "provider": "openrouter",
            "input_token_limit": 1_000_000,
            "display_name": "Deep · Gemini 3.1 Pro",
        },
    }

    # Pre-tier model keys (pre-OpenRouter registry) mapped onto the tiers,
    # so saved sessions / stale forms / old report records keep working.
    LEGACY_MODEL_KEY_TIERS: dict[str, str] = {
        "gemini_3_1_pro": "standard",
        "gemini_3_5_flash": "fast",
        "gemini_3_1_flash_lite": "fast",
        "anthropic_claude_opus_4_8": "deep",
        "anthropic_claude_sonnet_4_6": "standard",
        "anthropic_claude_haiku_4_5": "fast",
        "openai_gpt_5_5": "standard",
        "openai_gpt_5_5_pro": "deep",
        "openai_gpt_5_4_mini": "fast",
        "xai_grok_4_3": "standard",
        "xai_grok_4_20": "standard",
        "openrouter_glm_5_2": "standard",
        "openrouter_glm_5_1": "fast",
        "openrouter_qwen3_7_max": "standard",
        "openrouter_qwen3_7_plus": "fast",
    }

    # ------------------------------------------------------------------
    # RAG / retrieval parameters
    # ------------------------------------------------------------------
    RAG_CHUNK_TOKENS: int = Field(default=512, ge=64)
    RAG_CHUNK_OVERLAP: int = Field(default=64, ge=0)
    RAG_MIN_CHARS: int = Field(default=300, ge=0)
    RAG_DISTANCE_L2: float = Field(default=2.0, gt=0.0)
    RAG_TOP_K: int = Field(default=12, ge=1)

    # ------------------------------------------------------------------
    # Chat / notes parameters
    # ------------------------------------------------------------------
    CHAT_DEDUPE_SIM_THRESHOLD: float = Field(default=0.9, ge=0.0, le=1.0)
    # Notes run the same reasoning tiers as reports — 2048 truncated them
    # mid-bullet (same failure class as the overview clamp).
    NOTES_MAX_TOKENS: int = Field(default=6000, ge=1)

    # ------------------------------------------------------------------
    # Blog generator
    # ------------------------------------------------------------------
    BLOG_MAX_TOKENS: int = Field(default=6000, ge=1)
    # Figure pipeline: a fast-tier planning call picks conceptual figures,
    # the writer drops :::figure blocks, and an image model renders them.
    # Every stage degrades gracefully (no plan → no figures; no image →
    # caption text), so disabling images never breaks blog generation.
    BLOG_IMAGES_ENABLED: bool = Field(default=True)
    BLOG_IMAGE_MODEL: str = Field(default="google/gemini-2.5-flash-image")
    BLOG_MAX_FIGURES: int = Field(default=3, ge=0)
    BLOG_PLAN_MAX_TOKENS: int = Field(default=800, ge=1)
    BLOG_TTL_SECONDS: int = Field(default=3600, ge=0)
    BLOG_CACHE_MAX_ENTRIES: int = Field(default=200, ge=1)

    # ==================================================================
    # Validators
    # ==================================================================

    @field_validator("ENV", mode="before")
    @classmethod
    def _lower_env(cls, v: str) -> str:
        return v.lower() if isinstance(v, str) else v

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: Any) -> Optional[str]:
        if not isinstance(v, str) or not v:
            return v
        # Hosts (Railway, Heroku, etc.) hand out bare postgres(ql):// URLs;
        # the async engine needs an explicit async driver.
        for scheme in ("postgres://", "postgresql://"):
            if v.startswith(scheme):
                return "postgresql+asyncpg://" + v[len(scheme) :]
        return v

    @field_validator("REQUIRE_LOGIN", mode="before")
    @classmethod
    def _parse_bool_loose(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in {"1", "true", "yes"}
        return bool(v)

    @field_validator("STRIP_REFERENCES", mode="before")
    @classmethod
    def _parse_strip_refs(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in {"1", "true", "yes"}
        return bool(v)

    @field_validator("CACHE_DIR", mode="before")
    @classmethod
    def _expand_cache_dir(cls, v: Any) -> Path:
        return Path(str(v)).expanduser() if v else Path(".cache")

    @field_validator("PROMPT_OVERRIDE_DIR", mode="before")
    @classmethod
    def _expand_prompt_dir(cls, v: Any) -> Path:
        return Path(str(v)).expanduser() if v else Path("")

    @model_validator(mode="after")
    def _compute_derived(self) -> Config:
        """Compute derived fields after all values are loaded."""
        # CSS_PREFIX defaults based on ENV
        if not self.CSS_PREFIX:
            self.CSS_PREFIX = "css/dist" if self.ENV == "prod" else "css"

        # Derived cache paths
        if str(self.VECTOR_CACHE_DIR) == ".":
            self.VECTOR_CACHE_DIR = self.CACHE_DIR / "vector"
        if str(self.SUMMARY_CACHE_DIR) == ".":
            self.SUMMARY_CACHE_DIR = self.CACHE_DIR / "summaries"

        # AUTH_REDIRECT_BASE_URL default
        if not self.AUTH_REDIRECT_BASE_URL:
            self.AUTH_REDIRECT_BASE_URL = f"http://localhost:{self.PORT}"

        # Per-tier OpenRouter model-ID overrides
        for tier, override in (
            ("fast", self.OPENROUTER_MODEL_FAST),
            ("standard", self.OPENROUTER_MODEL_STANDARD),
            ("deep", self.OPENROUTER_MODEL_DEEP),
        ):
            if override and override.strip():
                self.AVAILABLE_MODELS[tier]["id"] = override.strip()

        # CHAT_TEMPERATURE falls back to LLM_TEMPERATURE if not explicitly set
        # (handled by env var fallback in the old code; here both default to 0.7)

        return self

    def resolve_model_key(self, key: str | None) -> str | None:
        """Map a user-supplied model key to a registry key.

        Accepts current tier keys (case-insensitive) and pre-tier legacy
        registry keys via :pydata:`LEGACY_MODEL_KEY_TIERS`. Returns ``None``
        for anything unrecognised.
        """
        if not key:
            return None
        for valid_key in self.AVAILABLE_MODELS:
            if valid_key.lower() == key.lower():
                return valid_key
        return self.LEGACY_MODEL_KEY_TIERS.get(key.lower())

    def validate_model_registry(self) -> None:
        """Validate model registry shape before serving traffic."""
        if self.DEFAULT_MODEL_KEY not in self.AVAILABLE_MODELS:
            raise ValueError(
                f"DEFAULT_MODEL_KEY '{self.DEFAULT_MODEL_KEY}' is not in AVAILABLE_MODELS"
            )

        for key, info in self.AVAILABLE_MODELS.items():
            missing = {"id", "provider", "input_token_limit", "display_name"} - set(
                info
            )
            if missing:
                raise ValueError(
                    f"Model '{key}' is missing required fields: {sorted(missing)}"
                )
            if info["provider"] not in _VALID_PROVIDERS:
                raise ValueError(
                    f"Model '{key}' uses unsupported provider '{info['provider']}'"
                )
            if (
                not isinstance(info["input_token_limit"], int)
                or info["input_token_limit"] <= 0
            ):
                raise ValueError(f"Model '{key}' has invalid input_token_limit")


# Instantiate config for easy import
config = Config()

# Block startup in non-dev environments with insecure session secret
if config.SESSION_SECRET == "dev-insecure-secret":
    if config.ENV not in ("dev", "test"):
        import sys as _sys

        print(
            "\n[FATAL] OH_SESSION_SECRET is set to the insecure default!\n"
            "Set OH_SESSION_SECRET to a strong random value (e.g. `python -c "
            '"import secrets; print(secrets.token_urlsafe(32))"`).\n',
            file=_sys.stderr,
        )
        _sys.exit(1)
    elif config.ENV == "dev":
        import warnings as _warnings

        _warnings.warn(
            "OH_SESSION_SECRET is set to the insecure default. "
            "Set it to a strong random value before deploying.",
            UserWarning,
            stacklevel=1,
        )

__all__ = ["Config", "config"]
