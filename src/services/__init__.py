# This file makes the 'services' directory a Python package.
"""High-level business logic layer.

Sub-packages:
• report  - PDF/HTML parsing + summarisation
• chat    - chat history & moderation
• notes   - study-notes generation
• paper   - text extraction + caching
• core    - shared provider clients (LLM, Exa, …)

Re-exports below mean an app can import top-level services without
knowing the internal layout:

```python
from services import ReportGenerator, DirectQueryService, LLMService
```
"""
from services.core.llm_service import LLMService  # noqa: F401 - re-export
from services.report.report_generator import ReportGenerator  # noqa: F401 - re-export
from services.report.direct_query_service import DirectQueryService  # noqa: F401 - re-export

__all__: list[str] = [
    "ReportGenerator",
    "DirectQueryService",
    "LLMService",
]
