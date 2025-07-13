"""Shared FastAPI dependency helpers.

Route modules should import these helpers instead of reaching directly into
`app.py` for the singleton instances.
"""

from config import Config as AppConfig
from infra.exa_client import ExaClient
from infra.llm_service import LLMService
from infra.text.text_extractor import TextExtractor


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
    """Return the singleton :class:`~infra.text_extractor.TextExtractor`."""
    from infra.text.text_extractor import TextExtractor  # import here to dodge circulars

    return TextExtractor.instance()
