"""Low-level FastAPI route definitions.

Unlike `api.routers` the modules here don’t necessarily expose a configured
APIRouter; they may define individual `@router.get` / `@post` handlers or even
stand-alone `FastAPI()` instances for internal use (e.g. health checks).

They are imported by `api.routers` or directly by `main.py`.
"""
