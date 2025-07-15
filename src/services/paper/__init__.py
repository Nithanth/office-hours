"""Paper text extraction & caching layer.

Responsibilities:
• Download PDF / HTML.
• Parse into plain text.
• Persist on-disk cache to accelerate repeat summarisation.

Most callers only need ``TextExtractor.instance()`` which is re-exported here
for convenience:

```python
from services.paper import TextExtractor
```
"""

from services.paper.text_extractor import TextExtractor  # noqa: F401 - re-export

__all__: list[str] = ["TextExtractor"]
