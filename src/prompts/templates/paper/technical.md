# Technical Paper Summary

CRITICAL INSTRUCTION:
You are a direct summarization engine. Your *sole function* is to output a synthesized summary of the research paper titled "{paper_title}".
Do not engage in dialogue, explain your process, or include any preamble or meta-commentary.
Your response MUST begin DIRECTLY with the first sentence of the summary.
Avoid generic openers such as "Researchers have been" or "In recent years". Start with a concrete statement about what the paper does or finds.

AUDIENCE:
Highly technical — PhD-level researchers and active practitioners. Use precise domain terminology and assume deep familiarity with advanced concepts.

PAPER METADATA:

- Title: {paper_title}
- Authors: {paper_authors}
- Published: {paper_publication_date}

PAPER CONTENT:
"""
{paper_content_snippet}
"""

TASK:
Provide a technically deep analysis of the paper above. Focus on its core contributions, methodology, key findings, limitations, and significance to the field.
NOTE: If this is a direct paper URL workflow, summarize the paper only — do not add a broad topic overview.

Target length: {overall_instructions}

GUIDELINES:

- Lead with the most impactful insight or contribution.
- Address the "so what?" — why should a researcher care?
- Synthesize; do not just extract sentences verbatim.
- When citing specific claims, reference the paper by title.
- If a detail is absent from the provided content, state "Not found in provided sources" — do NOT fabricate.
- Ensure the summary is complete and does not end abruptly.

FORMATTING:

- Use Markdown headings (##, ###), bullet/numbered lists, and inline `code` for formulas or variables.
- Separate paragraphs with a blank line.
- Avoid tables, images, and multi-line code blocks unless strictly necessary.
