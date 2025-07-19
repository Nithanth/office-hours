"""Lux Scholar notes JSON schema (v1).

Moved here so all shared Pydantic models live under the top-level ``models``
package.  Import with::

    from models.notes import NotesDoc, Section, Bullet
"""

from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class Bullet(BaseModel):
    """A single bullet item, optionally with nested children."""

    text: str
    sub: Optional[List["Bullet"]] = None  # Nested bullets (depth +1)
    ordered: bool = False  # True → render as a numbered list instead of a bullet

    # Silently ignore unknown keys so the schema can evolve without hard breaks.
    model_config = ConfigDict(extra="ignore")


# Forward-reference resolution
Bullet.model_rebuild()


class Section(BaseModel):
    """High-level section such as HIGH-LEVEL OVERVIEW or PAPER 1."""

    heading: str = Field(..., min_length=1)
    bullets: List[Bullet] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class NotesDoc(BaseModel):
    """Root document consisting of multiple sections."""

    sections: List[Section] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
