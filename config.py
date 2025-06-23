import os
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()  # Takes environment variables from .env.


# Configuration Models
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
    VERSION: str = "0.0.1"
    YEAR: int = 2025  # Added for copyright year
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

    # Application specific configurations from design doc
    MAX_PAPERS_PER_SEARCH: int = 20
    SUMMARY_MAX_LENGTH: int = 300  # Max words for a paper summary in the report list
    MAX_CONCURRENT_PAPER_SUMMARIES: int = int(os.getenv("MAX_CONCURRENT_PAPER_SUMMARIES", 3))
    PAPER_MAX_CONTENT_SNIPPET_CHARS: int = int(
        os.getenv("PAPER_MAX_CONTENT_SNIPPET_CHARS", 3900000)
    )  # Increased to allow full paper content

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
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", 120))

    # Exa retry/backoff settings (Phase 3 hardening)
    EXA_MAX_RETRIES: int = int(os.getenv("EXA_MAX_RETRIES", 3))
    EXA_RETRY_MIN_WAIT: int = int(os.getenv("EXA_RETRY_MIN_WAIT", 1))  # seconds
    EXA_RETRY_MAX_WAIT: int = int(os.getenv("EXA_RETRY_MAX_WAIT", 10))  # seconds

    DEFAULT_MODEL_KEY: str = "gemini_2_5_pro_preview"

    AVAILABLE_MODELS: dict[str, dict[str, Any]] = {
        # Gemini Models
        "gemini_2_5_pro_preview": {
            "id": "gemini-2.5-pro-preview-05-06",
            "provider": "gemini",
            "input_token_limit": 1000000,  # Estimated, Gemini models have large context windows
            "display_name": "Gemini 2.5 Pro (Preview)",
        },
        "gemini_2_5_flash_preview": {
            "id": "gemini-2.5-flash-preview-05-20",
            "provider": "gemini",
            "input_token_limit": 1000000,  # Estimated
            "display_name": "Gemini 2.5 Flash (Preview)",
        },
        "gemini_2_0_flash": {
            "id": "gemini-2.0-flash",
            "provider": "gemini",
            "input_token_limit": 128000,  # Estimated
            "display_name": "Gemini 2.0 Flash",
        },
        # Anthropic Models
        "anthropic_claude_opus_4_preview": {
            "id": "claude-opus-4-20250514",
            "provider": "anthropic",
            "input_token_limit": 200000,  # Anthropic models typically have large context
            "display_name": "Claude Opus 4 (Preview)",
        },
        "anthropic_claude_sonnet_4_preview": {
            "id": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "input_token_limit": 200000,
            "display_name": "Claude Sonnet 4 (Preview)",
        },
        "anthropic_claude_3_7_sonnet_latest": {
            "id": "claude-3.7-sonnet-latest",
            "provider": "anthropic",
            "input_token_limit": 200000,
            "display_name": "Claude 3.7 Sonnet (Latest)",
        },
        # OpenAI Models
        "openai_gpt_4_1": {
            "id": "gpt-4.1",
            "provider": "openai",
            "input_token_limit": 128000,  # Assuming similar to GPT-4 Turbo
            "display_name": "OpenAI GPT-4.1",
        },
        "openai_gpt_4o": {
            "id": "gpt-4o",
            "provider": "openai",
            "input_token_limit": 128000,
            "display_name": "OpenAI GPT-4o",
        },
        "openai_o4_mini": {  # User provided ID 'o4-mini'
            "id": "o4-mini",
            "provider": "openai",
            "input_token_limit": 128000,  # Estimated, assuming similar to GPT-4o context
            "display_name": "OpenAI o4-mini",
        },
        "openai_o3_mini": {  # User provided ID 'o3-mini'
            "id": "o3-mini",
            "provider": "openai",
            "input_token_limit": 16000,  # Estimated, assuming similar to GPT-3.5 Turbo context
            "display_name": "OpenAI o3-mini",
        },
        # Together AI Models
        "together_deepseek_r1": {
            "id": "deepseek-ai/DeepSeek-R1",
            "provider": "together_ai",
            "input_token_limit": 128000,
            "display_name": "DeepSeek R1 (TogetherAI)",
        },
        "together_qwen3_235b": {
            "id": "Qwen/Qwen3-235B-A22B-fp8-tput",
            "provider": "together_ai",
            "input_token_limit": 128000,
            "display_name": "Qwen3 235B (TogetherAI)",
        },
        "together_deepseek_v3": {
            "id": "deepseek-ai/DeepSeek-V3",
            "provider": "together_ai",
            "input_token_limit": 131000,
            "display_name": "DeepSeek V3 (TogetherAI)",
        },
        "together_llama_4_maverick": {
            "id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "provider": "together_ai",
            "input_token_limit": 950000,
            "display_name": "LLaMA-4 Maverick 17B (TogetherAI)",
        },
        "together_llama_4_scout": {
            "id": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
            "provider": "together_ai",
            "input_token_limit": 950000,
            "display_name": "LLaMA-4 Scout 17B (TogetherAI)",
        },
        "together_llama_3_3_70b_turbo": {
            "id": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
            "provider": "together_ai",
            "input_token_limit": 80000,  # Assuming Llama 3.1+ context size
            "display_name": "LLaMA-3.3 70B Turbo (TogetherAI)",
        },
    }

    # Fallback model for TogetherAI if the primary fails (Optional)
    # You can choose one of your available TogetherAI models or another reliable one.
    TOGETHER_FALLBACK_MODEL: Optional[str] = "gpt-4o"

    # --- Prompt Helper Strings (Static parts of the prompts) ---
    _CRITICAL_INSTRUCTION_TOPIC_TEMPLATE_STR: str = """CRITICAL INSTRUCTION:
You are a direct summarization engine. Your *sole function* is to output the summary text itself for the research topic: "{query_text}".
You do not engage in dialogue, explain your process, or include any preamble, conversational text, or meta-commentary.
Your response MUST begin DIRECTLY with the first word of the synthesized topic overview. For example, if the topic is "Quantum Computing", your response must start like: "Quantum computing is an emerging field..."
Do not use markdown, bolding, or any special formatting in your output.
"""

    _CRITICAL_INSTRUCTION_PAPER_TEMPLATE_STR: str = """CRITICAL INSTRUCTION:
You are a direct summarization engine. Your *sole function* is to output a synthesized prose summary for the research paper titled "{paper_title}".
You do not engage in dialogue, explain your process, or include any preamble, conversational text, or meta-commentary.
Your response MUST begin DIRECTLY with the first sentence of the synthesized prose summary.
Do not use markdown, bolding, or any special formatting in your output.
"""

    _CONTENT_QUALITY_INSTRUCTIONS_STR: str = """
- Lead with the most interesting or impactful insights.
- Use concrete examples or analogies to clarify complex points, especially for semi-technical and non-technical audiences.
- Clearly address the "so what?" question – why should the reader care about this information?
- Ensure the summary flows logically and tells a coherent story.
"""

    _FORMATTING_INSTRUCTIONS_STR: str = """
- Output plain text only.
- Do not use markdown, bolding, italics, or any special formatting.
- Ensure paragraphs are well-formed.
"""

    _GENERAL_PROMPT_INSTRUCTIONS_STR: str = """
- Adopt a third-person, objective, analytical, and informative tone.
- Synthesize information rather than just extracting verbatim.
- Ensure the summary is complete and does not end abruptly.
"""

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

    _FINAL_ADHERENCE_INSTRUCTION_STR: str = """IMPORTANT FINAL INSTRUCTION:
Your response MUST begin with the first word of the summary itself. No preceding text, dialogue, or meta-commentary is allowed. Adhere strictly to all instructions.
"""

    # --- Main Topic Prompt Templates ---
    TOPIC_SUMMARY_PROMPT_TEMPLATE_TECHNICAL: str = (
        _CRITICAL_INSTRUCTION_TOPIC_TEMPLATE_STR + "\n\nTASK OVERVIEW:\n"
        "You are tasked with generating a comprehensive and technically deep overview of the research topic specified in the critical instructions. "
        "This summary is intended for a highly technical audience, such as PhDs or active researchers in the relevant field. "
        "Your explanation should delve into the core concepts, prevalent methodologies, significant findings, current challenges, and potential future research directions. "
        + _HYBRID_INPUT_HANDLING_INSTRUCTIONS_STR
        + "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )

    TOPIC_SUMMARY_PROMPT_SEMI_TECH: str = (
        _CRITICAL_INSTRUCTION_TOPIC_TEMPLATE_STR + "\n\nTASK OVERVIEW:\n"
        "You are tasked with generating an insightful and accessible overview of the research topic specified in the critical instructions. "
        "This summary is for a semi-technical audience (e.g., product managers, investors with some technical background). "
        "Explain the core concepts, key implications, potential applications, and major advancements. Avoid excessive jargon but maintain accuracy. "
        + _HYBRID_INPUT_HANDLING_INSTRUCTIONS_STR
        + "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )

    TOPIC_SUMMARY_PROMPT_NON_TECH: str = (
        _CRITICAL_INSTRUCTION_TOPIC_TEMPLATE_STR + "\n\nTASK OVERVIEW:\n"
        "You are tasked with generating a clear and concise overview of the research topic specified in the critical instructions. "
        "This summary is for a non-technical audience (e.g., general business stakeholders, VCs without a deep tech background). "
        "Explain the fundamental ideas, why it's important, and its potential real-world impact in simple terms. Avoid jargon entirely. "
        + _HYBRID_INPUT_HANDLING_INSTRUCTIONS_STR
        + "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )

    # --- Main Paper Prompt Templates ---
    PAPER_SUMMARY_PROMPT_TEMPLATE_TECHNICAL: str = (
        _CRITICAL_INSTRUCTION_PAPER_TEMPLATE_STR + "\n\nCONTEXT OF THE PAPER:\n"
        "Authors: {paper_authors}\n"
        "Publication Date: {paper_publication_date}\n"
        'Content Snippet for Focus:\n"""\n{paper_content_snippet}\n"""\n\n'
        "TASK OVERVIEW:\n"
        "Provide a synthesized, technically deep analysis of the research paper detailed above. "
        "This summary is for a highly technical audience (e.g., PhDs, active researchers in the field). "
        "Focus on its core contributions, methodology, key findings, limitations, and its significance to the field. "
        "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )

    PAPER_SUMMARY_PROMPT_SEMI_TECH: str = (
        _CRITICAL_INSTRUCTION_PAPER_TEMPLATE_STR + "\n\nCONTEXT OF THE PAPER:\n"
        "Authors: {paper_authors}\n"
        "Publication Date: {paper_publication_date}\n"
        'Content Snippet for Focus:\n"""\n{paper_content_snippet}\n"""\n\n'
        "TASK OVERVIEW:\n"
        "Provide an insightful and accessible summary of the research paper detailed above. "
        "This summary is for a semi-technical audience (e.g., product managers, investors with some technical background). "
        "Focus on its main takeaways, implications, potential applications, and overall importance. Avoid excessive jargon but maintain accuracy. "
        "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )

    PAPER_SUMMARY_PROMPT_NON_TECH: str = (
        _CRITICAL_INSTRUCTION_PAPER_TEMPLATE_STR + "\n\nCONTEXT OF THE PAPER:\n"
        "Authors: {paper_authors}\n"
        "Publication Date: {paper_publication_date}\n"
        'Content Snippet for Focus:\n"""\n{paper_content_snippet}\n"""\n\n'
        "TASK OVERVIEW:\n"
        "Provide a clear and concise summary of the research paper detailed above. "
        "This summary is for a non-technical audience (e.g., general business stakeholders, VCs without a deep tech background). "
        "Focus on what the paper is about in simple terms, its main conclusions, and why it matters. Avoid jargon entirely. "
        "The designated target audience is: {target_audience}.\n"
        "Please adhere to the following length guidance: {overall_instructions}\n"
        "\nCONTENT AND STYLE GUIDELINES:\n"
        + _CONTENT_QUALITY_INSTRUCTIONS_STR
        + _GENERAL_PROMPT_INSTRUCTIONS_STR
        + "\nFORMATTING REQUIREMENTS:\n"
        + _FORMATTING_INSTRUCTIONS_STR
        + _FINAL_ADHERENCE_INSTRUCTION_STR
    )


# Instantiate config for easy import
config = Config()
