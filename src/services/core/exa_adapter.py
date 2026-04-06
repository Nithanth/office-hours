"""Thin wrapper around *exa_py* ≥2.0 with stable snake_case API and structured logging."""

from typing import Any, Final

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from utils.observability import _METRICS_ENABLED, EXA_LAT

try:
    from exa_py import Exa
except ModuleNotFoundError as exc:  # pragma: no cover - dev mis-config
    raise ImportError(
        "The 'exa_py' package is required but not installed. Add it to your "
        "environment or requirements.txt."
    ) from exc


class ExaAdapter:
    """SDK wrapper exposing Exa search/similar/answer/research through a stable interface.

    Updated for exa_py 2.x:
    - search() replaces search_and_contents() (now returns text by default)
    - find_similar() replaces find_similar_and_contents()
    - use_autoprompt removed (no longer a parameter)
    - contents= dict replaces top-level text=/summary=/highlights= params
    """

    _RETRIABLE_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
        ConnectionError,
        TimeoutError,
    )

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Exa API key is required to instantiate ExaAdapter.")
        self._sync: Exa = Exa(api_key=api_key)

    # ---------------------------------------------------------------------
    # Search (replaces search_and_contents in 2.x)
    # ---------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def search(self, query: str, **kwargs: Any) -> Any:  # noqa: ANN401 - SDK types
        """Unified search via :py:meth:`exa_py.Exa.search`.

        In 2.x, search() returns text contents by default. Pass
        ``contents=False`` for metadata-only, or
        ``contents={"text": True, "summary": True}`` for specific content.
        """
        # Strip removed params that callers might still pass
        kwargs.pop("use_autoprompt", None)
        logger.debug("Exa search", query=query, params=kwargs)
        if _METRICS_ENABLED and EXA_LAT is not None:
            with EXA_LAT.time():
                return self._sync.search(query, **kwargs)
        return self._sync.search(query, **kwargs)

    # Backward-compat alias for callers that haven't migrated yet
    search_and_contents = search

    # ------------------------------------------------------------------
    # Find similar (replaces find_similar_and_contents in 2.x)
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def find_similar(self, url: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Find pages similar to *url* via :py:meth:`exa_py.Exa.find_similar`.

        In 2.x, find_similar() returns text contents by default.
        """
        kwargs.pop("use_autoprompt", None)
        logger.debug("Exa find_similar", url=url, params=kwargs)
        if _METRICS_ENABLED and EXA_LAT is not None:
            with EXA_LAT.time():
                return self._sync.find_similar(url, **kwargs)
        return self._sync.find_similar(url, **kwargs)

    # ------------------------------------------------------------------
    # Get contents (still supported in 2.x)
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def get_contents(self, urls: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Retrieve contents for a list of URLs."""
        logger.debug("Exa get_contents", urls=urls, params=kwargs)
        if _METRICS_ENABLED and EXA_LAT is not None:
            with EXA_LAT.time():
                return self._sync.get_contents(urls, **kwargs)
        return self._sync.get_contents(urls, **kwargs)

    # ------------------------------------------------------------------
    # Answer endpoint
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def answer(self, question: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Short answer with supporting passages via :py:meth:`exa_py.Exa.answer`."""
        logger.debug("Exa answer", question=question, params=kwargs)
        if _METRICS_ENABLED and EXA_LAT is not None:
            with EXA_LAT.time():
                return self._sync.answer(question, **kwargs)
        return self._sync.answer(question, **kwargs)

    # ------------------------------------------------------------------
    # Research endpoint (new in 2.x)
    # ------------------------------------------------------------------

    @property
    def research(self) -> Any:
        """Access the Exa Research API client (async research tasks)."""
        return self._sync.research

    # -----------------------------------------------
    # Accessors
    # -----------------------------------------------
    @property
    def sync_client(self) -> Exa:
        """Expose the underlying synchronous SDK client (read-only)."""
        return self._sync
