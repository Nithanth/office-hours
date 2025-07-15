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
from types import ModuleType

# Prefer new location under ``api.routes`` but gracefully fall back to the old
# ``api.routers`` package so legacy tests still pass without separate shim
# modules.


# Helper that imports a router module, trying the new location first.
def _try_import(path_new: str, path_old: str) -> ModuleType:
    try:
        return _imp(path_new)
    except ModuleNotFoundError:
        return _imp(path_old)


chat = _try_import("api.routes.chat", "api.routers.chat")
report = _try_import("api.routes.report", "api.routers.report")

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
