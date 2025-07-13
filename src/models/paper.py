"""Pydantic model representing a research paper."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class Paper(BaseModel):
    """Pydantic model to represent a research paper."""

    id: str = Field(
        ...,
        description="Unique identifier, can be from Exa or other sources (e.g., Exa result ID or a canonical URL).",
    )
    title: str = Field(..., description="The title of the paper.")
    url: HttpUrl = Field(..., description="A valid URL to the paper.")

    authors: Optional[list[str]] = Field(
        default_factory=list, description="A list of author names."
    )
    text_content: Optional[str] = Field(
        default=None,
        description="The main textual content of the paper (e.g., abstract or full text retrieved from the source like Exa's 'text' field).",
    )
    published_date: Optional[datetime | str] = Field(
        default=None,
        description="Publication date (datetime or ISO string). Pydantic will coerce ISO strings to datetime objects; strings are retained for backward compatibility.",
    )
    score: Optional[float] = Field(
        default=None, description="Relevance score from the search provider."
    )

    # Path to cached full-text file (filled by TextExtractor when available)
    full_text_path: Optional[str] = Field(
        default=None,
        description="Absolute path to cached plaintext extracted from the paper (if any).",
    )

    source: str = Field(
        default="Unknown",
        description="Source of the paper data (e.g., 'Exa Search', 'Manual Entry').",
    )

    def __repr__(self) -> str:
        return (
            f"Paper(id='{self.id}', "
            f"title='{self.title[:50] + '...' if len(self.title) > 50 else self.title}', "
            f"url='{self.url}', "
            f"authors_count={len(self.authors) if self.authors else 0}, "
            f"published_date='{self.published_date}', "
            f"score={self.score}, "
            f"source='{self.source}')"
        )

    class Config:
        """Pydantic model configuration."""

        from_attributes = True

