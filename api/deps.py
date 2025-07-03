"""Shared FastAPI dependency helpers.

Route modules should import these helpers instead of reaching directly into
`app.py` for the singleton instances.  This avoids tight coupling to the
bootstrap module and makes the dependencies easier to mock during tests.
"""

from __future__ import annotations

# Singletons are still created inside ``app.py`` during application start-up.
# We import the module lazily inside each helper so that we always return the
# latest instance (they are assigned during the lifespan event).
from config import Config as AppConfig
from services.exa_client import ExaClient
from services.llm_client import LLMService
from services.text_extractor import TextExtractor


def get_settings() -> AppConfig:
    """Return global immutable settings object for DI.

    Wrapped in a helper so tests can easily override via `dependency_overrides`.
    """

    # NOTE: we import the *instance* created at the bottom of ``config.py`` –
    # **not** the Config class.  This keeps memory footprint tiny and matches
    # existing ``config.config`` semantics.
    from config import config as _settings

    return _settings


def get_llm_service() -> LLMService | None:
    """Return the shared :class:`~services.llm_client.LLMService` instance."""
    import app  # local import to avoid circulars at import time

    return app.llm_service_instance


def get_exa_client() -> ExaClient | None:
    """Return the shared :class:`~services.exa_client.ExaClient` instance."""
    import app  # local import to avoid circulars

    return app.exa_client_instance


def get_paper_service() -> TextExtractor:
    """Return the singleton :class:`~services.text_extractor.TextExtractor`."""
    from services.text_extractor import TextExtractor  # import here to dodge circulars

    return TextExtractor.instance()
