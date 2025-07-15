"""Notes service package - exposes `NotesGenerator`."""

# Direct import is cheap; `NotesGenerator` itself lazily calls LLM when used.

from .notes_generator import NotesGenerator

__all__: list[str] = ["NotesGenerator"]
