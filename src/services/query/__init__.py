"""LLM-powered *Ask Anything* answering service.

Wraps the `DirectQueryService` so API routers can import succinctly:

```python
from services.query import DirectQueryService
```

The service takes a user research question, grounds it via Exa search,
and synthesises an HTML answer using the primary LLM provider.
"""

import importlib as _importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # For static type checkers only – avoids runtime circular import.
    from services.query.direct_query_service import DirectQueryService  # noqa: F401


# Mypy requires a return type.  We return ``Any`` because the attribute could be
# the target service class or raise an error.
def __getattr__(name: str) -> Any:  # noqa: D401 – PEP 562 lazy import
    """Dynamically import DirectQueryService on first access."""

    if name == "DirectQueryService":
        module = _importlib.import_module("services.query.direct_query_service")
        return module.DirectQueryService
    raise AttributeError(name)


__all__: list[str] = ["DirectQueryService"]
