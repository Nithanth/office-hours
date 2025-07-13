"""Collection of APIRouter objects mounted by the main FastAPI app.

Each module defines and exports a *router* variable; on startup `main.py`
iterates the list and includes them. Importing this package does **not**
register routers automatically - it is intentionally side-effect-free.

Convenience re-export of common routers so tests can quickly mount a subset:

```python
from api.routers import report, chat
```
"""

# Lazy import to avoid heavy service initialisation during unit-collection.

from importlib import import_module as _imp

chat = _imp("api.routers.chat")  # type: ignore[attr-defined]
report = _imp("api.routers.report")  # type: ignore[attr-defined]
search = _imp("api.routers.search")  # type: ignore[attr-defined]

__all__: list[str] = ["chat", "report", "search"]
