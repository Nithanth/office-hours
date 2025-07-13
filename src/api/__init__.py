"""HTTP layer built on FastAPI.

Directory layout:
• `routes/`   - low-level endpoints (often no prefix) that group related paths.
• `routers/`  - higher-level APIRouters mounted by `main.app` (e.g. /search).

Nothing is executed at import-time; we only expose the sub-modules so tools and
IDE autocompletion work:

```python
from api import routes, routers
```
"""

from importlib import import_module as _imp

routes = _imp("api.routes")  # type: ignore[attr-defined]
routers = _imp("api.routers")  # type: ignore[attr-defined]

__all__: list[str] = ["routes", "routers"]
