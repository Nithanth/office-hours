"""Prompt template helpers for Lux Scholar LLM workflows."""

from typing import Any, Optional

# -------------------------------
# Default system prompt
# -------------------------------
DEFAULT_SYSTEM_PROMPT = (
    "You are Lux Scholaris, an advanced AI research assistant. "
    "Your purpose is to provide clear, comprehensive, and insightful analysis of academic research. "
    "Generate responses that are educational, well-structured, and tailored to the user's query. "
    "When synthesizing information, clearly distinguish between established facts, hypotheses, and potential areas for future research. "
    "Maintain a tone suitable for researchers and semi-technical (non-PhD) investors who are looking to learn more about a potentially complex topic."
)

# ------------------------------------------------------------------
# Workflow-specific prompt factories
# ------------------------------------------------------------------

def get_paper_deep_dive_prompts(
    title: str,
    authors: list[str],
    abstract: str,
    full_text_snippet: Optional[str] = None,  # Snippet of main content, if available
    paper_url: Optional[str] = None,
    target_audience: Optional[str] = None,  # e.g., "non-technical manager", "peer researcher"
    output_format: Optional[str] = None,  # e.g., "bullet points for key findings"
    **kwargs: Any,  # For future flexibility
) -> tuple[str, str]:
    """Generates system and user prompts for a deep dive analysis of a single research paper."""

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if target_audience:
        system_prompt += f" Tailor your explanation for a {target_audience}."

    user_prompt_parts = [
        "Please provide a comprehensive deep dive analysis of the following research paper:"
    ]
    if paper_url:
        user_prompt_parts.append(f"- URL: {paper_url}")
    user_prompt_parts.append(f"- Title: {title}")
    user_prompt_parts.append(f"- Authors: {', '.join(authors) if authors else 'N/A'}")
    user_prompt_parts.append(f"- Abstract: {abstract}")
    if full_text_snippet:
        user_prompt_parts.append(f"- Key Content Snippet:\n{full_text_snippet}")

    user_prompt_parts.extend(
        [
            "\nYour analysis should cover the following aspects in a well-structured format:",
            "1.  **Introduction & Problem Statement:** Briefly, what problem does the paper address and why is it important?",
            "2.  **Core Contributions:** What are the main novel contributions or claims of this paper?",
            "3.  **Methodology:** Describe the key methodologies, techniques, or approaches used.",
            "4.  **Key Findings & Results:** Summarize the most important findings and results presented.",
            "5.  **Discussion & Implications:** Discuss the implications of these findings. How do they advance the field?",
            "6.  **Limitations:** What are the limitations of the study as stated by the authors or apparent from the work?",
            "7.  **Future Work:** What directions for future research are suggested or implied?",
            "8.  **Overall Significance & Novelty:** Provide an assessment of the paper's overall significance and novelty.",
        ]
    )

    if output_format:
        user_prompt_parts.append(
            f"\nPlease ensure the section on key findings is presented as {output_format}."
        )

    user_prompt = "\n".join(user_prompt_parts)
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
    summary_size_key: str = "M",  # S, M, L, XL - controls heading granularity
    **kwargs: Any,
) -> tuple[str, str]:
    """Generates prompts for a landscape analysis of a research topic."""

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if target_audience:
        system_prompt += f" Tailor your explanation for a {target_audience}."

    user_prompt_parts = [
        f"Please provide a comprehensive landscape analysis of the research topic: '{topic}'."
    ]
    if context_snippets:
        user_prompt_parts.append(
            f"\nConsider the following context from relevant paper snippets:\n{context_snippets}"
        )
    if scope:
        user_prompt_parts.append(f"Focus specifically on: {scope}.")
    if overall_instructions:
        user_prompt_parts.append(
            f"\nFollow these overall instructions for the summary: {overall_instructions}"
        )

    # ------------------------------------------------------------------
    # Tailor section list based on summary_size_key
    # ------------------------------------------------------------------
    size_key = summary_size_key.upper()
    if size_key == "S":
        section_headings = [
            "Topic Overview:",
            "Key Findings:",
            "Potential Impact:",
        ]
    elif size_key in ("L", "XL"):
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
    else:  # default M
        section_headings = [
            "Topic Overview:",
            "Current State of Research:",
            "Key Methodologies:",
            "Significant Findings & Discoveries:",
            "Major Challenges & Open Questions:",
            "Emerging Trends & Future Directions:",
            "Potential Applications & Impact:",
        ]

    user_prompt_parts.extend(
        [
            (
                "Your analysis should synthesize insights from approximately "
                f"{num_papers_to_synthesize} diverse and significant papers on this topic."
            ),
            "",  # Spacer line for readability
            "Use the following EXACT heading structure (plain text, no Markdown symbols):",
            *section_headings,
            "",  # Blank line
            "Formatting rules (strict):",
            "- Do NOT use any Markdown markup (e.g., '#', '*', '-', '1.').",
            "- Start each section with its exact heading text (as shown above) followed by a single blank line, then write cohesive prose paragraphs. Do not use bullet or numbered lists inside the sections.",
            "- Maintain a formal, third-person, objective tone. Avoid first-person narration or conversational fillers.",
            "",  # Blank line
            "Example section format:",
            "Topic Overview:",
            "Implicit Neural Representations (INRs) are continuous functions parameterized by neural networks. They offer memory efficiency because ... (continue prose).",
            "",  # Spacer
            "Current State of Research:",
        ]
    )
    user_prompt = "\n".join(user_prompt_parts)
    return system_prompt, user_prompt


def get_query_resolution_prompts(
    query: str, num_supporting_papers: int = 3, target_audience: Optional[str] = None, **kwargs: Any
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


DIRECT_QUERY_USER_PROMPT = """
User Question: {user_question}

Supporting Evidence:
{supporting_evidence}

Please provide a direct answer to the user's question based on the evidence provided.
"""

# New, more detailed prompt for the 'Direct Query' feature to act as a research assistant
RESEARCH_ASSISTANT_PROMPT = {
    "system": """
        You are an expert research assistant. Your goal is to provide a direct, concise, and well-supported answer to the user's question.
        - Synthesize information from the provided paper snippets to formulate your answer.
        - When you use information from a paper, you MUST cite it using its source ID, like this: [Source ID].
        - If the provided papers do not contain the answer, state that the information is not available in the provided context.
        - Do not make up information. Base your answer strictly on the evidence provided.
        - Present the final answer in clear, well-structured markdown.
    """,
    "user": """Please answer the following research question:

Question: "{user_question}"

Here are the supporting paper snippets to use as evidence:

{supporting_evidence}
""",
}

# Mapping of audience segments to concrete prompt templates
AUDIENCE_PROMPT_MAP = {
    "technical": {
        "topic": load_template("topic", "technical"),
        "paper": load_template("paper", "technical"),
        "system_suffix": (
            "Your reader is a PhD-level researcher, post-doctoral fellow, or professor. "
            "Use precise domain terminology and assume deep familiarity with advanced concepts."
        ),
    },
    "semi_technical": {
        "topic": load_template("topic", "semi_technical"),
        "paper": load_template("paper", "semi_technical"),
        "system_suffix": (
            "Your reader is an under-graduate student or working professional in tech (e.g., a product manager). "
            "Keep jargon moderate and briefly explain specialised terms."
        ),
    },
    "non_technical": {
        "topic": load_template("topic", "non_technical"),
        "paper": load_template("paper", "non_technical"),
        "system_suffix": (
            "Your reader has no deep technical background (think first-year undergrad). "
            "Use plain language, everyday analogies, and metaphor where helpful. Focus on the big-picture significance."
        ),
    },
}

# ------------------------------------------------------------------
# Inline demo code removed – use dedicated tests instead.
# ------------------------------------------------------------------
