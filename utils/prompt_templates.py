from typing import Any, Optional

# Use built-in types for List and Tuple as per Ruff UP006/UP035

# --- Default System Prompts ---
# DEFAULT_SYSTEM_PROMPT: A general-purpose system prompt providing the base persona and instructions for the AI.
# This can be prepended to more specific user prompts.
DEFAULT_SYSTEM_PROMPT = (
    "You are Lux Scholaris, an advanced AI research assistant. "
    "Your purpose is to provide clear, comprehensive, and insightful analysis of academic research. "
    "Generate responses that are educational, well-structured, and tailored to the user's query. "
    "When synthesizing information, clearly distinguish between established facts, hypotheses, and potential areas for future research. "
    "Maintain a tone suitable for researchers and semi-technical (non-PhD) investors who are looking to learn more about a potentially complex topic."
)


# --- Workflow-Specific Prompt Generation Functions ---


# get_paper_deep_dive_prompts: Generates system and user prompts specifically for conducting an in-depth analysis of a single research paper.
# This is intended for Workflow 3: Paper Deep Dive (see project design document).
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

    user_prompt_parts.extend(
        [
            (
                "Your analysis should synthesize insights from approximately "
                f"{num_papers_to_synthesize} diverse and significant papers on this topic."
            ),
            "",  # Spacer line for readability
            "Use the following EXACT heading structure (plain text, no Markdown symbols):",
            "Topic Overview:",
            "Current State of Research:",
            "Key Methodologies:",
            "Significant Findings & Discoveries:",
            "Major Challenges & Open Questions:",
            "Emerging Trends & Future Directions:",
            "Potential Applications & Impact:",
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


# --- Example Usage (for testing) ---
if __name__ == "__main__":
    # Paper Deep Dive Example
    sys_prompt_paper, user_prompt_paper = get_paper_deep_dive_prompts(
        title="Attention Is All You Need",
        authors=[
            "Ashish Vaswani",
            "Noam Shazeer",
            "Niki Parmar",
            "Jakob Uszkoreit",
            "Llion Jones",
            "Aidan N. Gomez",
            "Lukasz Kaiser",
            "Illia Polosukhin",
        ],
        abstract="The dominant sequence transduction models are based on complex recurrent or convolutional neural networks... We propose a new simple network architecture, the Transformer, based solely on attention mechanisms...",
        full_text_snippet="The Transformer allows for significantly more parallelization and can reach a new state of the art in translation quality after being trained for as little as twelve hours on eight P100 GPUs...",
        paper_url="https://arxiv.org/abs/1706.03762",
        target_audience="a machine learning practitioner",
    )
    print("--- PAPER DEEP DIVE ---")
    print(f"System Prompt:\n{sys_prompt_paper}")
    print(f"\nUser Prompt:\n{user_prompt_paper}")
    print("\n" + "=" * 50 + "\n")

    # Topic Analysis Example
    sys_prompt_topic, user_prompt_topic = get_topic_analysis_prompts(
        topic="Applications of Graph Neural Networks in Drug Discovery",
        num_papers_to_synthesize=3,
        scope="recent advancements and challenges",
        target_audience="a pharmaceutical industry analyst",
    )
    print("--- TOPIC ANALYSIS ---")
    print(f"System Prompt:\n{sys_prompt_topic}")
    print(f"\nUser Prompt:\n{user_prompt_topic}")
    print("\n" + "=" * 50 + "\n")

    # Query Resolution Example
    sys_prompt_query, user_prompt_query = get_query_resolution_prompts(
        query="Are Large Language Models capable of genuine common-sense reasoning?",
        num_supporting_papers=2,
        target_audience="a cognitive scientist",
    )
    print("--- QUERY RESOLUTION ---")
    print(f"System Prompt:\n{sys_prompt_query}")
    print(f"\nUser Prompt:\n{user_prompt_query}")
