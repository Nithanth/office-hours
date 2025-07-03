"""Temporary bridge module that exposes the FastAPI `app` defined in the legacy
`app.py` module.  This lets existing import paths (`from app import app`) keep
working while new code can import from `api.main`.

In the next sub-phase we will gradually copy route registration and middleware
setup here and shrink the legacy `app.py` to a stub.  For now we simply
re-export.
"""

from __future__ import annotations

from api.routes import admin as admin_routes
from api.routes import ask_llm as ask_llm_routes
from api.routes import chat as chat_routes
from api.routes import pages as pages_routes
from api.routes import report as report_routes
from api.routes import search as search_routes
from app import app as legacy_app

# Mount modularised route groups
legacy_app.include_router(chat_routes.router)
legacy_app.include_router(report_routes.router)
legacy_app.include_router(search_routes.router)
legacy_app.include_router(pages_routes.router)
legacy_app.include_router(ask_llm_routes.router)
legacy_app.include_router(admin_routes.router)

app = legacy_app  # public re-export
