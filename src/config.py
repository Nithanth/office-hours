"""Centralised application configuration loaded from environment variables."""

import os
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

# Read variables from a local .env file (noop in prod).
load_dotenv()

# ---------------------------------------------------------------------------
# Pydantic models for nested config blobs
# ---------------------------------------------------------------------------

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
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")  # Google AI Studio API Key for Gemini
    # Path to your Google credentials JSON file - will be deferred for MVP
    GOOGLE_DOCS_CREDENTIALS_PATH: Optional[str] = os.getenv("GOOGLE_DOCS_CREDENTIALS_PATH")

    # Redis Configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", 3600))  # 1 hour default

    # Modal Configuration
    MODAL_TOKEN: Optional[str] = os.getenv("MODAL_TOKEN")
    MODAL_WORKSPACE: Optional[str] = os.getenv("MODAL_WORKSPACE")
    MODAL_TOKEN_ID: Optional[str] = os.getenv("MODAL_TOKEN_ID")
    MODAL_TOKEN_SECRET: Optional[str] = os.getenv("MODAL_TOKEN_SECRET")

    # Application Settings
    APP_TITLE: str = "Lux Scholar"
    VERSION: str = "0.1.0"
    YEAR: int = 2025  # Added for copyright year
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "info")
    LOG_FORMAT_JSON: bool = os.getenv("LUX_LOG_FORMAT_JSON", "False").lower() == "true"
    DEFAULT_PAPER_COUNT: int = int(os.getenv("DEFAULT_PAPER_COUNT", 3))
    DEFAULT_SIMILAR_PAPER_COUNT: int = int(os.getenv("DEFAULT_SIMILAR_PAPER_COUNT", 3))  # Added for similar paper searches
    RELOAD_APP: bool = os.getenv("RELOAD_APP", "True").lower() == "true"
    MAX_AUTHORS_DISPLAY: int = 5  # Max authors to display before 'et al.'

    # Application specific configurations from design doc
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
        "XL": "highly comprehensive and exhaustive, suitable for deep understanding and including extensive details, context, and implications",
    }

    SUMMARY_OUTPUT_TOKEN_TARGETS: dict[str, dict[str, int]] = {
        "technical": {
            "S": 2000,  # Reverting to generous upper bound (was 500, orig 250)
            "M": 4000,  # Reverting to generous upper bound (was 1000, orig 500)
            "L": 6000,  # Reverting to generous upper bound (was 1500, orig 800)
            "XL": 10000,  # Reverting to generous upper bound (was 2500, orig 1300)
        },
        "semi_technical": {
            "S": 2000,
            "M": 4000,
            "L": 6000,
            "XL": 10000,
        },
        "non_technical": {
            "S": 2000,
            "M": 4000,
            "L": 6000,
            "XL": 10000,
        },
    }

    # LLM Configuration
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", 0.7))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", 3))
    # Async semaphore limit per LLM provider to avoid QPS throttling.
    MAX_CONCURRENT_REQUESTS_PER_PROVIDER: int = int(os.getenv("MAX_CONCURRENT_REQUESTS_PER_PROVIDER", 4))
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", 120))

    # Topic summary tuning
    MAX_TOPIC_SUMMARY_TOKENS: int = int(os.getenv("MAX_TOPIC_SUMMARY_TOKENS", 2048))

    # Chat specific tuning
    CHAT_MAX_TOKENS: int = int(os.getenv("CHAT_MAX_TOKENS", 512))
    EXCERPT_CHARS: int = int(os.getenv("EXCERPT_CHARS", 2000))

    # Exa retry/backoff settings (Phase 3 hardening)
    EXA_MAX_RETRIES: int = int(os.getenv("EXA_MAX_RETRIES", 3))
    EXA_RETRY_MIN_WAIT: int = int(os.getenv("EXA_RETRY_MIN_WAIT", 1))  # seconds
    EXA_RETRY_MAX_WAIT: int = int(os.getenv("EXA_RETRY_MAX_WAIT", 10))  # seconds

    # Default model - can be overridden via env var.
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

    # Fallback model for TogetherAI if the primary fails (Optional)
    # You can choose one of your available TogetherAI models or another reliable one.
    TOGETHER_FALLBACK_MODEL: Optional[str] = "gpt-4o"

    # ---------------------------------------------------------------------
    # Prompt helper strings (used by prompt templates; keep in sync)
    # ---------------------------------------------------------------------

    # (1) Critical instruction for *topic* summaries – forbids any pre-amble,
    #     enforces plain-text output, and references the ``{query_text}``
    #     placeholder which is substituted when the prompt is built.
    _CRITICAL_INSTRUCTION_TOPIC_TEMPLATE_STR: str = """CRITICAL INSTRUCTION:
You are a direct summarization engine. Your *sole function* is to output the summary text itself for the research topic: "{query_text}".
You do not engage in dialogue, explain your process, or include any preamble, conversational text, or meta-commentary.
Your response MUST begin DIRECTLY with the first word of the synthesized topic overview. For example, if the topic is "Quantum Computing", your response must start like: "Quantum computing is an emerging field..."
Do not use markdown, bolding, or any special formatting in your output.
"""

    # (2) Critical instruction for *paper* summaries – variant tailored to an
    #     individual paper; uses the ``{paper_title}`` placeholder.
    _CRITICAL_INSTRUCTION_PAPER_TEMPLATE_STR: str = """CRITICAL INSTRUCTION:
You are a direct summarization engine. Your *sole function* is to output a synthesized prose summary for the research paper titled "{paper_title}".
You do not engage in dialogue, explain your process, or include any preamble, conversational text, or meta-commentary.
Your response MUST begin DIRECTLY with the first sentence of the synthesized prose summary.
Do not use markdown, bolding, or any special formatting in your output.
"""

    # (3) Content-quality checklist – reminders to lead with insights.
    _CONTENT_QUALITY_INSTRUCTIONS_STR: str = """
- Lead with the most interesting or impactful insights.
- Use concrete examples or analogies to clarify complex points, especially for semi-technical and non-technical audiences.
- Clearly address the "so what?" question - why should the reader care about this information?
- Ensure the summary flows logically and tells a coherent story.
"""

    # (4) Output-formatting guardrails – enforce plain text.
    _FORMATTING_INSTRUCTIONS_STR: str = """
- Output plain text only.
- Do not use markdown, bolding, italics, or any special formatting.
- Ensure paragraphs are well-formed.
"""

    # (5) Global style guidelines – objective, analytical tone.
    _GENERAL_PROMPT_INSTRUCTIONS_STR: str = """
- Adopt a third-person, objective, analytical, and informative tone.
- Synthesize information rather than just extracting verbatim.
- Ensure the summary is complete and does not end abruptly.
"""

    # (6) Hybrid-input handling – differentiate topics vs. direct queries.
    _HYBRID_INPUT_HANDLING_INSTRUCTIONS_STR: str = """

UNDERSTANDING THE USER'S INTENT (TOPIC vs. DIRECT QUERY):
The user's input "{query_text}" might be a broad research topic or a specific question. Your first step is to discern this intent to tailor your summary effectively.

1. IF THE INPUT IS A BROAD RESEARCH TOPIC:
   - Focus: Provide a comprehensive overview. Synthesize key themes, methodologies, significant findings, challenges, and future directions from the provided paper snippets.
   - Goal: Educate the user on the breadth and depth of the topic.
   - Example:
     - User Input (Broad Topic): "Applications of AI in drug discovery"
     - Summary Should Prioritize: Overview of AI techniques used, stages of drug discovery impacted, major successes, current limitations, and emerging AI trends in the field.
     - Ideal Summary Start Example: "Artificial intelligence is revolutionizing drug discovery by..."

2. IF THE INPUT IS A DIRECT QUESTION:
   - Focus: Directly answer the question using information synthesized from the provided paper snippets. The papers serve as evidence or sources for your answer.
   - Goal: Provide a clear, concise, and evidence-based answer to the user's specific query.
   - Example:
     - User Input (Direct Question): "How is reinforcement learning used to optimize chemical synthesis pathways?"
     - Summary Should Prioritize: Explaining the mechanism of RL in this context, specific examples of its application to synthesis pathway optimization, benefits observed (e.g., efficiency, novelty), and any challenges in its implementation, all drawn from the papers.
     - Ideal Summary Start Example: "Reinforcement learning optimizes chemical synthesis pathways by..."

Adapt your synthesis and the structure of your summary based on this determination.
"""

    # (7) Final adherence reminder – appended last to guard output.
    _FINAL_ADHERENCE_INSTRUCTION_STR: str = """IMPORTANT FINAL INSTRUCTION:
Your response MUST begin with the first word of the summary itself. No preceding text, dialogue, or meta-commentary is allowed. Adhere strictly to all instructions.
"""


# Instantiate config for easy import
config = Config()
