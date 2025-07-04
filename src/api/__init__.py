"""API package exposing the FastAPI application.

During the initial refactor we re-export the legacy `app` instance defined in
`app.py` so that external entry-points (e.g. `uvicorn app:app`) continue to
work unchanged.

In later phases we will migrate routes into `api.routes.*` and eventually
replace the re-export with a freshly constructed FastAPI object defined in
`api/main.py`.
"""

from __future__ import annotations

__all__: list[str] = []  # `api` package no longer re-exports FastAPI app
