"""Shared FastAPI dependency helpers.

Route modules should import these helpers instead of reaching directly into
`app.py` for the singleton instances.
"""

from typing import cast

from fastapi import Request

from config import Config as AppConfig
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMService
from services.paper.text_extractor import TextExtractor


def get_settings() -> AppConfig:
    """Return global immutable settings object for DI.

    Wrapped in a helper so tests can easily override via `dependency_overrides`.
    """
    from config import config as _settings

    return _settings


def get_llm_service() -> LLMService | None:
    """Return the shared :class:`~infra.llm_service.LLMService` instance."""
    import main as _main  # local import to avoid circulars at import time

    return _main.llm_service_instance


def get_exa_client() -> ExaClient | None:
    """Return the shared :class:`~infra.exa_client.ExaClient` instance."""
    import main as _main  # local import to avoid circulars

    return _main.exa_client_instance


def get_paper_service() -> TextExtractor:
    """Return the singleton :class:`~infra.text_extractor.TextExtractor` instance."""
    return TextExtractor.instance()


async def get_session_id(request: "Request") -> str:  # noqa: D401 – FastAPI Depends
    """Return the session-id set by the ChatSessionMiddleware (or 'anon')."""

    return getattr(request.state, "session_id", "anon")


async def get_current_user_id(request: "Request") -> str | None:
    """Return durable authenticated user id, if login is configured and present."""

    from services.auth.session import get_auth_user_id

    return cast(str | None, get_auth_user_id(request))
