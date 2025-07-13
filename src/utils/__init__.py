"""Shared utility helpers.

This package groups small, stateless helpers used across multiple
services (HTML rendering, semantic deduplication, search utilities …).

Public re-exports are provided for the 90 % use-cases so callers can do:

```python
from utils import text_to_html_paragraphs, deduplicate_chat
```
"""

from .html_builder import text_to_html_paragraphs
from .dedupe import deduplicate_chat

__all__: list[str] = [
    "text_to_html_paragraphs",
    "deduplicate_chat",
]
