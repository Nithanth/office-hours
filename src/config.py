"""Centralised application configuration loaded from environment variables."""

import datetime
import os
from pathlib import Path
from typing import Any, Optional, cast

from dotenv import load_dotenv

# Load .env only in development/local environments to avoid accidental leakage
if os.getenv("ENV", "dev") != "prod":  # Treat any non-"prod" value as dev
    load_dotenv()


class Config:
    # API Keys
    EXA_API_KEY: Optional[str] = os.getenv("EXA_API_KEY")
    TOGETHER_API_KEY: Optional[str] = os.getenv("TOGETHER_API_KEY")
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

    # --- App runtime settings ---
    # --- Environment and asset prefixes ---
    ENV: str = os.getenv("ENV", "dev").lower()
    # Front-end CSS folder: use purged bundle in production for smaller payload.
    CSS_PREFIX: str = os.getenv("CSS_PREFIX", "css/dist" if ENV == "prod" else "css")

    APP_TITLE: str = "Nithu's PhD Student"
    VERSION: str = "0.1.0"
    YEAR: int = int(os.getenv("YEAR", datetime.datetime.utcnow().year))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
    LOG_FORMAT_JSON: bool = os.getenv("LUX_LOG_FORMAT_JSON", "False").lower() == "true"

    # --- Cache directories ---
    # All on-disk caches live under a single root to simplify deployment and cleanup.
    CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", ".cache")).expanduser()
    VECTOR_CACHE_DIR: Path = CACHE_DIR / "vector"
    SUMMARY_CACHE_DIR: Path = CACHE_DIR / "summaries"

    # --- Embedding / Vector store optimisations ---
    # Persist embeddings as float16 instead of float32 to halve disk usage.
    EMB_FLOAT16: bool = os.getenv("EMB_FLOAT16", "0") == "1"
    # Override the batch size used during SentenceTransformer.encode when building indexes.
    EMB_BATCH_SIZE: int = int(os.getenv("EMB_BATCH_SIZE", 32))

    # When enabled, store query embeddings on disk so they persist across restarts.
    DISK_QUERY_CACHE: bool = os.getenv("DISK_QUERY_CACHE", "0") == "1"

    # --- PDF download ---
    # Send If-Modified-Since headers on repeat PDF fetches to avoid re-downloading unchanged files.
    USE_COND_FETCH: bool = os.getenv("USE_COND_FETCH", "0") == "1"

    # --- Chat behaviour ---
    # Max tokens per assistant chunk (UI override)
    CHAT_MAX_TOKENS: int = int(os.getenv("CHAT_MAX_TOKENS", 512))
    # Temperature when chat endpoint is used directly (falls back to LLM_TEMPERATURE)
    CHAT_TEMPERATURE: float = float(
        os.getenv("CHAT_TEMPERATURE", os.getenv("LLM_TEMPERATURE", 0.7))
    )

    # Optional manual worker count (warn-only)
    UVICORN_WORKERS: int | None = (
        int(cast(str, os.getenv("UVICORN_WORKERS"))) if os.getenv("UVICORN_WORKERS") else None
    )

    # How many candidates we ask Exa for per search before post-filtering.
    # UI paginates these in blocks of 5, so 60 gives up to 12 pages.
    DEFAULT_PAPER_COUNT: int = int(os.getenv("DEFAULT_PAPER_COUNT", 60))
    DEFAULT_SIMILAR_PAPER_COUNT: int = int(
        os.getenv("DEFAULT_SIMILAR_PAPER_COUNT", 3)
    )  # Added for similar paper searches
    RELOAD_APP: bool = os.getenv("RELOAD_APP", "True").lower() == "true"
    MAX_AUTHORS_DISPLAY: int = 5  # Max authors to display before 'et al.'

    # --- Runtime & chat stream ---
    CHAT_STREAM_BUFFER: int = int(os.getenv("CHAT_STREAM_BUFFER", 100))
    CHAT_KEEPALIVE_SEC: int = int(os.getenv("CHAT_KEEPALIVE_SEC", 1))

    # Concurrency for warming full-text caches on startup/search
    TEXT_PREWARM_CONCURRENCY: int = int(os.getenv("TEXT_PREWARM_CONCURRENCY", 8))

    # Logging & observability
    LOG_FILE: str | None = os.getenv("LOG_FILE")
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "0") == "1"

    # Vector store & RAG fine-tuning
    RAG_SEARCH_MULTIPLIER: int = int(os.getenv("RAG_SEARCH_MULTIPLIER", 25))
    CE_RERANK_POOL: int = int(os.getenv("CE_RERANK_POOL", 40))
    ENABLE_CE_RERANK: bool = os.getenv("ENABLE_CE_RERANK", "0") == "1"

    EXA_POOL_SIZE: int = int(os.getenv("EXA_POOL_SIZE", 35))

    # PDF/Text processing
    STRIP_REFERENCES: bool = os.getenv("STRIP_REFERENCES", "0") in {"1", "true", "yes", "True"}

    # Caching layer
    PAPER_CACHE_DB: str | None = os.getenv("PAPER_CACHE_DB")

    # Prompt builder
    PROMPT_OVERRIDE_DIR: Path = Path(os.getenv("PROMPT_OVERRIDE_DIR", "")).expanduser()

    # Admin & session security – no defaults, must be set in prod
    ADMIN_API_KEY: str | None = os.getenv("ADMIN_API_KEY")
    LUX_SESSION_SECRET: str = os.getenv("LUX_SESSION_SECRET", "dev-insecure-secret")

    # --- Paper/report tuning ---
    MAX_PAPERS_PER_SEARCH: int = 20
    SUMMARY_MAX_LENGTH: int = 300  # words
    MAX_CONCURRENT_PAPER_SUMMARIES: int = int(os.getenv("MAX_CONCURRENT_PAPER_SUMMARIES", 4))
    # Hard cap on characters passed from a paper into any LLM prompt (≈15k tokens).
    PAPER_MAX_CONTENT_SNIPPET_CHARS: int = int(os.getenv("PAPER_MAX_CONTENT_SNIPPET_CHARS", 150000))
    LLM_TIMEOUT_SECONDS: int = int(os.getenv("LLM_TIMEOUT_SECONDS", 120))
    # Optional UX pause when serving cached summaries – set to 0 to disable.
    CACHED_SUMMARY_DELAY_SECONDS: float = float(os.getenv("CACHED_SUMMARY_DELAY_SECONDS", "1.5"))
    # Maximum concurrent PDF/HTML extraction tasks when pre-fetching full text.
    # Increase if your deployment has ample CPU/network bandwidth.
    PDF_EXTRACTION_CONCURRENCY: int = int(os.getenv("PDF_EXTRACTION_CONCURRENCY", 6))

    # --- PDF extraction ---
    # Maximum size (in megabytes) of a PDF we attempt to download & parse.
    # Increased from 30 → 50 MB to accommodate larger arXiv PDFs without manual
    # env overrides. Set the `MAX_PDF_MB` env-var to a different value at
    # runtime if needed.
    MAX_PDF_MB: int = int(os.getenv("MAX_PDF_MB", 100))
    # Minimum characters for a full-text extract to be considered good quality.
    MIN_FULL_TEXT_CHARS: int = int(os.getenv("MIN_FULL_TEXT_CHARS", 3000))
    # Minimum characters to accept as *partial* fallback (e.g. abstract).
    MIN_PARTIAL_TEXT_CHARS: int = int(os.getenv("MIN_PARTIAL_TEXT_CHARS", 400))

    # OCR fallback – enable only when `tesseract` binary is present.
    ENABLE_PDF_OCR: bool = os.getenv("ENABLE_PDF_OCR", "False").lower() == "true"
    # Maximum PDF size (MB) we attempt OCR on (avoid huge scans).
    OCR_MAX_PDF_MB: int = int(os.getenv("OCR_MAX_PDF_MB", 10))

    QUALITATIVE_LENGTH_GUIDANCE: dict[str, str] = {
        "S": "brief and concise (≈ 300 words), summarizing only the absolute key takeaways",
        "M": "moderately detailed (≈ 600 words), covering the main points comprehensively but without excessive depth on any single one",
        "L": "detailed and thorough (≈ 900 words), exploring nuances and providing substantial supporting information for each key aspect",
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

    # Hybrid summarisation ---------------------------------------------------
    # Papers shorter than this many characters include **full text** in the prompt;
    # longer ones fall back to RAG-based evidence snippets to save tokens.
    HYBRID_FULL_CONTEXT_CHAR_THRESHOLD: int = int(
        os.getenv("HYBRID_FULL_CONTEXT_CHAR_THRESHOLD", 50_000)
    )

    # How many chunks per themed query to inject for the hybrid path.
    HYBRID_RAG_TOPK: int = int(os.getenv("HYBRID_RAG_TOPK", 3))

    # --- LLM request defaults ---
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", 0.7))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", 3))
    # Async semaphore limit per LLM provider to avoid QPS throttling.
    MAX_CONCURRENT_REQUESTS_PER_PROVIDER: int = int(
        os.getenv("MAX_CONCURRENT_REQUESTS_PER_PROVIDER", 4)
    )
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", 120))

    # Topic summary tuning
    # Feature toggles -------------------------------------------------------
    # When True, URL-based reports that include at least `URL_THEME_MIN_PAPERS`
    # user-selected similar papers will prepend a short "common theme" overview
    # generated by the LLM.
    ENABLE_URL_SIMILAR_OVERVIEW: bool = (
        os.getenv("ENABLE_URL_SIMILAR_OVERVIEW", "False").lower() == "true"
    )
    # Minimum number of similar papers required to trigger the common-theme
    # summarisation when `ENABLE_URL_SIMILAR_OVERVIEW` is active.
    URL_THEME_MIN_PAPERS: int = int(os.getenv("URL_THEME_MIN_PAPERS", "3"))

    MAX_TOPIC_SUMMARY_TOKENS: int = int(os.getenv("MAX_TOPIC_SUMMARY_TOKENS", 2048))

    # --- Chat prompt sizing ---
    EXCERPT_CHARS: int = 15000

    # --- Exa API back-off ---
    EXA_MAX_RETRIES: int = int(os.getenv("EXA_MAX_RETRIES", 3))
    EXA_RETRY_MIN_WAIT: int = int(os.getenv("EXA_RETRY_MIN_WAIT", 1))  # seconds
    EXA_RETRY_MAX_WAIT: int = int(os.getenv("EXA_RETRY_MAX_WAIT", 10))  # seconds

    # --- Default/fallback LLM models ---
    DEFAULT_MODEL_KEY: str = os.getenv("DEFAULT_MODEL_KEY", "gemini_2_5_pro")

    AVAILABLE_MODELS: dict[str, dict[str, Any]] = {
        # Gemini (Google)
        "gemini_2_5_pro": {
            "id": "models/gemini-2.5-pro-preview-06-05",
            "provider": "gemini",
            "input_token_limit": 1_000_000,
            "display_name": "Gemini 2.5 Pro",
        },
        "gemini_2_5_flash": {
            "id": "models/gemini-2.5-flash-preview-05-20",
            "provider": "gemini",
            "input_token_limit": 1_000_000,
            "display_name": "Gemini 2.5 Flash",
        },
        # Anthropic Claude
        "anthropic_claude_opus_4": {
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
        # OpenAI
        "openai_gpt_4o": {
            "id": "gpt-4o",
            "provider": "openai",
            "input_token_limit": 128_000,
            "display_name": "GPT-4o",
        },
        "openai_gpt_4_1": {
            "id": "gpt-4.1",
            "provider": "openai",
            "input_token_limit": 1_000_000,
            "display_name": "GPT-4.1",
        },
        "openai_o3": {
            "id": "o3",
            "provider": "openai",
            "input_token_limit": 200_000,
            "display_name": "o3",
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
            "display_name": "Llama 4 Maverick (TogetherAI)",
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

    # --- RAG / retrieval parameters ---
    RAG_CHUNK_TOKENS: int = int(os.getenv("RAG_CHUNK_TOKENS", 512))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", 64))
    RAG_MIN_CHARS: int = int(os.getenv("RAG_MIN_CHARS", 300))
    RAG_DISTANCE_L2: float = float(os.getenv("RAG_DISTANCE_L2", 2.0))
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", 12))

    # --- Chat / notes parameters ---
    CHAT_DEDUPE_SIM_THRESHOLD: float = float(os.getenv("CHAT_DEDUPE_SIM_THRESHOLD", 0.9))
    NOTES_MAX_TOKENS: int = int(os.getenv("NOTES_MAX_TOKENS", 2048))

    __all__: list[str] = ["Config", "config"]


# Instantiate config for easy import
config = Config()

# Warn loudly if deploying with insecure session secret
if config.ENV == "prod" and config.LUX_SESSION_SECRET == "dev-insecure-secret":
    import logging as _logging

    _logging.critical(
        "LUX_SESSION_SECRET is set to the insecure default in production! "
        "Set LUX_SESSION_SECRET to a strong random value."
    )
