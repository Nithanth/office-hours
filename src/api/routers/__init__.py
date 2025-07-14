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

chat = _imp("api.routers.chat")
report = _imp("api.routers.report")

# Commonly-used standalone routers (stream + notes) – re-export for tests
report_stream = _imp("api.routers.report_stream")
report_notes = _imp("api.routers.report_notes")

# Base public symbols
__all__: list[str] = [
    "chat",
    "report",
    "report_stream",
    "report_notes",
]

# *Optional* search router (may be absent in some builds)
try:
    search = _imp("api.routers.search")
    __all__.append("search")
except ModuleNotFoundError:
    # Silently skip when not available
    pass
