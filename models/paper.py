"""
models/paper.py

This module defines the Pydantic model for a 'Paper'.
It's used to structure and validate data related to research papers
retrieved from various sources or created within the application.
"""

from datetime import datetime
from typing import Any, Optional

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


# Example Usage (for direct testing of this module):
if __name__ == "__main__":
    # Example 1: Paper with all fields
    paper_data_exa: dict[str, Any] = {
        "id": "exa_paper_123",
        "title": "Sample Paper Title",
        "url": "https://example.com/paper123",
        "authors": ["Author A", "Author B"],
        "text_content": "This is the main text content of the paper, which could be an abstract or the full text.",
        "published_date": "2023-01-15T14:30:00",
        "score": 0.95,
        "source": "Exa Search",
    }
    try:
        paper_exa = Paper(**paper_data_exa)
        print("--- Example Paper from Exa Data ---")
        print(paper_exa.model_dump_json(indent=2))
        assert paper_exa.text_content is not None
        print(f"Retrieved text content: {paper_exa.text_content[:100]}...")
    except Exception as e:
        print(f"Error creating paper_exa: {e}")

    # Example 2: Paper with minimal fields
    paper_data_minimal: dict[str, Any] = {
        "id": "minimal_paper_456",
        "title": "Another Paper",
        "url": "https://example.com/paper456",
        "text_content": None,  # Explicitly None
    }
    try:
        paper_minimal = Paper(**paper_data_minimal)
        print("\nMinimal Paper Object created successfully:")
        print(paper_minimal.model_dump_json(indent=2))
        print(f"\nMinimal paper authors (should be None): {paper_minimal.authors}")
        print(f"Minimal paper text content (should be None): {paper_minimal.text_content}")
    except Exception as e:
        print(f"Error creating paper_minimal: {e}")

    # Example 3: Invalid URL
    paper_data_invalid_url: dict[str, Any] = {
        "id": "invalid_url_789",
        "title": "Paper with a bad URL",
        "url": "htp:/badurl.com",  # Invalid URL scheme
        "source": "Test Case",
    }
    print("\nAttempting to create paper with invalid URL:")
    try:
        paper_invalid = Paper(**paper_data_invalid_url)
        print(paper_invalid.model_dump_json(indent=2))
    except Exception as e:
        print(f"Error (expected): {e}")
