"""Prompt building utilities.

Phase-1: foundation for modular prompt composition without touching the
existing `ReportGenerator` flows.  Subsequent phases can progressively
migrate to this builder to gain flexibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.paper import Paper
from prompts.prompt_templates import (
    AUDIENCE_PROMPT_MAP,
    RESEARCH_ASSISTANT_PROMPT,
)

__all__ = [
    "Block",
    "PromptBuilder",
    "get_query_resolution_prompts",
]


@dataclass(slots=True)
class Block:
    """A self-contained chunk of prompt text.

    Args:
        title: Optional section title.  If provided, the renderer will
            emit it followed by a newline.
        body:  The main content of the block.
        sep:   Delimiter placed **between** blocks when the prompt is
            rendered.  Defaults to a blank line (two newlines).
    """

    body: str
    title: str | None = None
    sep: str = "\n\n"

    def render(self) -> str:  # pragma: no cover – trivial
        if self.title:
            return f"{self.title}\n{self.body.strip()}"
        return self.body.strip()


@dataclass
class PromptBuilder:
    """Collects blocks then renders a full prompt string."""

    blocks: list[Block] = field(default_factory=list)

    def add(self, body: str, *, title: str | None = None, sep: str = "\n\n") -> PromptBuilder:
        self.blocks.append(Block(body=body, title=title, sep=sep))
        return self

    def extend(self, block: Block) -> PromptBuilder:
        self.blocks.append(block)
        return self

    def render(self) -> str:
        if not self.blocks:
            return ""
        rendered: list[str] = [self.blocks[0].render()]
        for blk in self.blocks[1:]:
            rendered.append(blk.sep + blk.render())
        return "".join(rendered).strip()


def get_query_resolution_prompts(
    user_question: str,
    supporting_papers: list[Paper],
    audience_type: str,
    max_content_chars: int,
) -> tuple[str, str]:
    """Builds the system and user prompts for the direct query resolution feature."""

    # Prepare the supporting evidence string
    evidence_str = ""
    if supporting_papers:
        evidence_items = []
        for paper in supporting_papers:
            # Ensure content is not None before slicing
            content_snippet = (
                (paper.text_content[:max_content_chars] + "...")
                if paper.text_content
                else "Content not available."
            )
            evidence_items.append(
                f"[Source ID: {paper.id}]\nTitle: {paper.title}\nContent: {content_snippet}"
            )
        evidence_str = "\n\n".join(evidence_items)
    else:
        evidence_str = "No supporting papers provided."

    # Use the new research assistant prompt
    system_prompt = RESEARCH_ASSISTANT_PROMPT["system"]
    user_prompt = RESEARCH_ASSISTANT_PROMPT["user"].format(
        user_question=user_question, supporting_evidence=evidence_str
    )

    # Append audience-specific instructions to the system prompt
    audience_instructions = AUDIENCE_PROMPT_MAP.get(audience_type, {}).get("system_suffix", "")
    if audience_instructions:
        system_prompt = f"{system_prompt}\n\n{audience_instructions}"

    return system_prompt, user_prompt


def get_paper_deep_dive_prompts(
    paper: Paper, audience_type: str, max_content_chars: int
) -> tuple[str, str]:
    """Placeholder for future deep-dive prompt builder.

    Currently unused. Raises NotImplementedError to catch accidental calls.
    """
    raise NotImplementedError("get_paper_deep_dive_prompts is not implemented yet.")
