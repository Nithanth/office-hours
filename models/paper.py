"""
models/paper.py

This module defines the Pydantic model for a 'Paper'.
It's used to structure and validate data related to research papers 
retrieved from various sources or created within the application.
"""
from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from datetime import date



class Paper(BaseModel):
    """Pydantic model to represent a research paper."""
    id: str = Field(..., description="Unique identifier, can be from Exa or other sources (e.g., Exa result ID or a canonical URL).")
    title: str = Field(..., description="The title of the paper.")
    url: HttpUrl = Field(..., description="A valid URL to the paper.")
    
    authors: Optional[List[str]] = Field(default_factory=list, description="A list of author names.")
    abstract: Optional[str] = Field(default=None, description="A summary or abstract of the paper.")
    published_date: Optional[str] = Field(default=None, description="The publication date as a string. Consider parsing to datetime.date if consistency is assured.")
    score: Optional[float] = Field(default=None, description="Relevance score from the search provider.")
    content: Optional[str] = Field(default=None, description="The full text content of the paper, if available.")
    
    source: str = Field(default="Unknown", description="Source of the paper data (e.g., 'Exa Search', 'Manual Entry').")

    class Config:
        """Pydantic model configuration."""
        # For Pydantic v2, orm_mode is replaced by from_attributes.
        # This allows the model to be created from arbitrary class instances that have matching attribute names.
        from_attributes = True 

# Example Usage (for direct testing of this module):
if __name__ == "__main__":
    # Example 1: Paper with all fields
    paper_data_exa = {
        "id": "exa_paper_123",
        "title": "The Future of AI in Scientific Discovery",
        "url": "https://example.com/papers/ai_future",
        "authors": ["Dr. Ada Lovelace", "Dr. Alan Turing"],
        "abstract": "This paper explores the potential of artificial intelligence to revolutionize scientific research methods and outcomes...",
        "published_date": "2023-10-26",
        "source": "Exa Search Example"
    }
    try:
        paper_obj = Paper(**paper_data_exa)
        print("Paper Object created successfully:")
        print(paper_obj.model_dump_json(indent=2))
    except Exception as e:
        print(f"Error creating paper_obj: {e}")

    # Example 2: Paper with minimal required fields
    paper_data_minimal = {
        "id": "minimal_456",
        "title": "A Brief Note on Quantum Entanglement",
        "url": "https://example.org/notes/quantum",
        # source will use default "Unknown" if not provided during direct instantiation,
        # but it's better to be explicit or handle it in the creation logic.
        # For this example, let's assume it's manually added.
        "source": "Manual Entry Example" 
    }
    try:
        paper_obj_minimal = Paper(**paper_data_minimal)
        print("\nMinimal Paper Object created successfully:")
        print(paper_obj_minimal.model_dump_json(indent=2))
        print(f"\nMinimal paper authors (should be None): {paper_obj_minimal.authors}")
        print(f"Minimal paper abstract (should be None): {paper_obj_minimal.abstract}")
    except Exception as e:
        print(f"Error creating paper_obj_minimal: {e}")

    # Example 3: Invalid URL
    paper_data_invalid_url = {
        "id": "invalid_url_789",
        "title": "Paper with a bad URL",
        "url": "htp:/badurl.com", # Invalid URL scheme
        "source": "Test Case"
    }
    print("\nAttempting to create paper with invalid URL:")
    try:
        paper_invalid = Paper(**paper_data_invalid_url)
        print(paper_invalid.model_dump_json(indent=2))
    except Exception as e:
        print(f"Error (expected): {e}")
