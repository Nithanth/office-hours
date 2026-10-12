"""Prompt template helpers for Office Hours LLM workflows."""

# ---------------------------------------------------------------------------
# Generic template loader (importlib.resources keeps paths portable)
# ---------------------------------------------------------------------------
from importlib.resources import files
from typing import Any, Optional, cast

import config as _cfg

# Optional directory where operators can mount custom prompt overrides
PROMPT_OVERRIDE_DIR = _cfg.config.PROMPT_OVERRIDE_DIR


def _load_template(rel_path: str) -> str:
    """Return the raw text of a prompt template.

    Lookup order:
    1. If ``PROMPT_OVERRIDE_DIR`` env var is set and the file exists there, load it.
    2. Fall back to the built-in template bundled under ``prompts/templates``.
    """

    # 1. Check override directory first
    if PROMPT_OVERRIDE_DIR:
        override_path = PROMPT_OVERRIDE_DIR / rel_path
        if override_path.is_file():
            return cast(str, override_path.read_text(encoding="utf-8").strip())

    # 2. Built-in templates packaged with the codebase
    tmpl_path = files("prompts").joinpath("templates").joinpath(rel_path)
    return cast(str, tmpl_path.read_text(encoding="utf-8").strip())


# -------------------------------
# Default system prompt
# -------------------------------

# Previously this was an in-code constant; now we load it from a markdown file
# so non-devs can tweak wording without a deployment.

DEFAULT_SYSTEM_PROMPT: str = _load_template("system/default.md")

# ---------------------------------------------------------------------------
# RAG workflow instruction blocks (used across multiple services)
# ---------------------------------------------------------------------------

# Preface for the direct query flow – appended before the question + evidence.
DIRECT_QUERY_USER_PREFIX: str = (
    "You are answering a direct research question for a user. "
    "Provide the best possible answer grounded in the provided source passages if they are relevant. "
    "If the passages are not relevant you may rely on your own knowledge. "
    "Answer in no more than 250 words (≈ one to two short paragraphs)."
)

# Instruction header for the study-notes generator.  This stays separate from
# the bulky report/chat text that follows, making the generator code cleaner.
STUDY_NOTES_INSTR_BLOCK: str = (
    "You are preparing LECTURE-STYLE OUTLINE NOTES for the user. "
    "Follow the nested bullet OUTPUT FORMAT rules in the system prompt. "
    "Structure each paper as a mini-lecture: context → core approach → results → limitations → connections. "
    "Nest DEEPLY (3-4 levels) — sub-bullets elaborate their parent, not just list facts. "
    "The notes must read cohesively top-to-bottom like attending a seminar. "
    "Include concrete numbers, architecture details, and dataset names. "
    "Weave chat insights naturally under the relevant concept. "
    "Show cross-paper connections with explicit signposting. "
    "Aim for 8-15 top-level bullets per paper with generous sub-nesting."
)


# ---------------------------------------------------------------------------
# Audience-specific template loader
# ---------------------------------------------------------------------------
def _audience_key(raw: str | None) -> str:
    """Normalize ``target_audience`` strings to one of our three keys."""

    if not raw:
        return "technical"

    key = raw.lower()
    if "non" in key and "technical" in key:
        return "non_technical"
    if "semi" in key and "technical" in key:
        return "semi_technical"
    return "technical"


# ---------------------------------------------------------------------------
# Audience prompt map – loads the real markdown files
# ---------------------------------------------------------------------------
def _aud_tmpl(section: str, audience: str) -> str:
    return _load_template(f"{section}/{audience}.md")


AUDIENCE_PROMPT_MAP = {
    "technical": {
        "topic": _aud_tmpl("topic", "technical"),
        "paper": _aud_tmpl("paper", "technical"),
        "system_suffix": (
            "Your reader is a PhD-level researcher, post-doctoral fellow, or professor. "
            "Use precise domain terminology and assume deep familiarity with advanced concepts."
        ),
    },
    "semi_technical": {
        "topic": _aud_tmpl("topic", "semi_technical"),
        "paper": _aud_tmpl("paper", "semi_technical"),
        "system_suffix": (
            "Your reader is an under-graduate student or working professional in tech (e.g., a product manager). "
            "Keep jargon moderate and briefly explain specialised terms."
        ),
    },
    "non_technical": {
        "topic": _aud_tmpl("topic", "non_technical"),
        "paper": _aud_tmpl("paper", "non_technical"),
        "system_suffix": (
            "Your reader has no deep technical background (think first-year undergrad). "
            "Use plain language, everyday analogies, and metaphor where helpful. Focus on the big-picture significance."
        ),
    },
}

# ---------------------------------------------------------------------------
# Deep-dive and topic analysis builders – now audience-aware
# ---------------------------------------------------------------------------


def get_paper_deep_dive_prompts(
    title: str,
    authors: list[str],
    abstract: str,
    full_text_snippet: Optional[str] = None,  # Snippet of main content, if available
    paper_url: Optional[str] = None,
    target_audience: Optional[
        str
    ] = None,  # e.g., "non-technical manager", "peer researcher"
    output_format: Optional[str] = None,  # e.g., "bullet points for key findings"
    **kwargs: Any,  # For future flexibility
) -> tuple[str, str]:
    """Generates system and user prompts for a deep dive analysis of a single research paper."""

    system_prompt = DEFAULT_SYSTEM_PROMPT
    aud_key = _audience_key(target_audience)
    if target_audience:
        system_prompt += f" Tailor your explanation for a {target_audience}."

    # Load audience-specific template and inject runtime fields.
    authors_str = ", ".join(authors) if authors else "N/A"
    user_tmpl = _aud_tmpl("paper", aud_key)

    user_prompt = user_tmpl.format(
        paper_url=paper_url or "",
        title=title,
        authors=authors_str,
        abstract=abstract,
        full_text_snippet=(full_text_snippet or ""),
        output_format=(output_format or ""),
    )
    return system_prompt, user_prompt


def get_topic_analysis_prompts(
    topic: str,
    num_papers_to_synthesize: int = 5,
    scope: Optional[
        str
    ] = None,  # e.g., "recent advancements in the last 2 years", "foundational concepts"
    target_audience: Optional[str] = None,
    context_snippets: Optional[str] = None,
    overall_instructions: Optional[str] = None,  # Added for length/style guidance
    summary_size_key: str = "M",  # S, M, L - controls heading granularity
    **kwargs: Any,
) -> tuple[str, str]:
    """Generates prompts for a landscape analysis of a research topic."""

    system_prompt = DEFAULT_SYSTEM_PROMPT
    aud_key = _audience_key(target_audience)
    if target_audience:
        system_prompt += f" Tailor your explanation for a {target_audience}."

    # ------------------------------
    # Build user prompt from template (audience-specific)
    # ------------------------------
    # Select heading list based on size key (retained for future use, ignored by template if absent)
    size_key = summary_size_key.upper()
    if size_key == "S":
        section_headings = [
            "Topic Overview:",
            "Key Findings:",
            "Potential Impact:",
        ]
    elif size_key == "L":
        section_headings = [
            "Topic Overview:",
            "Historical Context:",
            "Current State of Research:",
            "Key Methodologies:",
            "Significant Findings & Discoveries:",
            "Major Challenges & Open Questions:",
            "Emerging Trends & Future Directions:",
            "Potential Applications & Impact:",
            "Recommended Further Reading:",
        ]
    else:
        section_headings = [
            "Topic Overview:",
            "Current State of Research:",
            "Key Methodologies:",
            "Significant Findings & Discoveries:",
        ]

    tmpl = _aud_tmpl("topic", aud_key)

    context_block = (
        f"\nConsider the following context from relevant paper snippets:\n{context_snippets}"
        if context_snippets
        else ""
    )
    scope_block = f"Focus specifically on: {scope}." if scope else ""
    overall_block = (
        f"\nFollow these overall instructions for the summary: {overall_instructions}"
        if overall_instructions
        else ""
    )

    user_prompt = tmpl.format(
        query_text=topic,  # template uses {query_text}
        topic=topic,
        num_papers_to_synthesize=num_papers_to_synthesize,
        section_headings="\n".join(section_headings),
        context_block=context_block,
        scope_block=scope_block,
        overall_instructions=overall_instructions or "",
        target_audience=target_audience or aud_key.replace("_", "-"),
        overall_block=overall_block,
    )
    return system_prompt, user_prompt


def get_query_resolution_prompts(
    query: str,
    num_supporting_papers: int = 3,
    target_audience: Optional[str] = None,
    **kwargs: Any,
) -> tuple[str, str]:
    """Generates prompts for resolving a specific research query."""

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if target_audience:
        system_prompt += f" Tailor your explanation for a {target_audience}."

    user_prompt_parts = [
        f"Please provide a direct and evidence-based answer to the following research query: '{query}'."
    ]

    user_prompt_parts.extend(
        [
            f"Support your answer with synthesized findings from approximately {num_supporting_papers} relevant academic papers.",
            "\nYour response should include:",
            "1.  **Direct Answer:** A clear and concise answer to the query.",
            "2.  **Evidence Synthesis:** Summarize the key evidence from the literature that supports your answer. Cite or refer to the types of studies or findings.",
            "3.  **Conflicting Evidence/Nuances:** If there is conflicting evidence, or important nuances, please discuss them.",
            "4.  **Confidence Level:** Indicate the general level of confidence in the answer based on the available evidence (e.g., well-established, moderately supported, emerging, speculative).",
            "5.  **Knowledge Gaps:** Identify any remaining knowledge gaps or areas where more research is needed to fully answer the query.",
        ]
    )
    user_prompt = "\n".join(user_prompt_parts)
    return system_prompt, user_prompt


DIRECT_QUERY_USER_PROMPT: str = _load_template("query/direct_query_user.md")

# Detailed two-part template for the Direct-Query “research assistant” flow
RESEARCH_ASSISTANT_PROMPT: dict[str, str] = {
    "system": _load_template("query/research_assistant_system.md"),
    "user": _load_template("query/research_assistant_user.md"),
}

# ---------------------------------------------------------------------------
# Chat prompt constants
# ---------------------------------------------------------------------------

# Base system persona for paper-specific live Q&A chat sessions.
# Additional audience-specific suffixes are appended at runtime.
CHAT_SYSTEM_PROMPT: str = _load_template("chat/system.md")

# ------------------------------------------------------------------
# Inline demo code removed – use dedicated tests instead.
# ------------------------------------------------------------------
