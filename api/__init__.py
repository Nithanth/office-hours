"""API package exposing the FastAPI application.

During the initial refactor we re-export the legacy `app` instance defined in
`app.py` so that external entry-points (e.g. `uvicorn app:app`) continue to
work unchanged.

In later phases we will migrate routes into `api.routes.*` and eventually
replace the re-export with a freshly constructed FastAPI object defined in
`api/main.py`.
"""

from __future__ import annotations

from .main import app as app  # explicit re-export satisfies Ruff F401

# Re-export symbol list for `from api import *`
__all__ = ["app"]
