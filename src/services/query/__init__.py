"""LLM-powered *Ask Anything* answering service.

Wraps the `DirectQueryService` so API routers can import succinctly:

```python
from services.query import DirectQueryService
```

The service takes a user research question, grounds it via Exa search,
and synthesises an HTML answer using the primary LLM provider.
"""

from services.query.direct_query_service import DirectQueryService  # noqa: F401 – re-export

__all__: list[str] = ["DirectQueryService"]
