"""Prompt building utilities.

Phase-1: foundation for modular prompt composition without touching the
existing `ReportGenerator` flows.  Subsequent phases can progressively
migrate to this builder to gain flexibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Block",
    "PromptBuilder",
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
