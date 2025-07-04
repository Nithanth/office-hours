"""services/exa_adapter.py

A thin compatibility layer over the *exa_py* SDK.  The goal is to keep version
churn (e.g. endpoint renames, camel-vs-snake naming) isolated so the rest of the
codebase can continue to use a stable, snake_case parameter set.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    # The canonical synchronous & async clients shipped by exa_py.
    from exa_py import Exa
except ModuleNotFoundError as exc:  # pragma: no cover – dev mis-config
    raise ImportError(
        "The 'exa_py' package is required but not installed. Add it to your "
        "environment or requirements.txt."
    ) from exc

logger = logging.getLogger(__name__)


class ExaAdapter:  # pylint: disable=too-few-public-methods
    """SDK wrapper exposing *search_and_contents* through a stable interface."""

    _RETRIABLE_EXCEPTIONS: Final[
        tuple[type[Exception], ...]
    ] = ()  # TODO: Add real retryable errors

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Exa API key is required to instantiate ExaAdapter.")

        self._sync: Exa = Exa(api_key=api_key)
        self._async: Any | None = None  # Async client not available yet

    # ---------------------------------------------------------------------
    # Public (synchronous) helpers
    # ---------------------------------------------------------------------

    @retry(  # Simple exponential back-off for transient errors
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def search_and_contents(self, query: str, **kwargs: Any) -> Any:  # noqa: ANN401 – SDK types
        """Wrapper for :py:meth:`exa_py.Exa.search_and_contents`.

        Parameters are accepted in *snake_case* and passed directly to the SDK.
        """

        # Exa SDK expects snake_case parameter names. Passing kwargs directly
        # avoids validation errors such as "Invalid option: 'numResults'".
        params = kwargs
        logger.debug("ExaAdapter.search_and_contents %s – params=%s", query, params)
        return self._sync.search_and_contents(query, **params)

    # ------------------------------------------------------------------
    # Answer endpoint – short answers with supporting passages
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def answer(self, question: str, **kwargs: Any) -> Any:  # noqa: ANN401 – SDK types
        """Wrapper for :py:meth:`exa_py.Exa.answer`.

        Returns Exa SDK *Answer* object which contains an `answer` string and
        supporting `results` list.
        """
        logger.debug("ExaAdapter.answer %s – params=%s", question, kwargs)
        return self._sync.answer(question, **kwargs)

    # ------------------------------------------------------------------
    # Async variant – handy for future performance improvements.
    # ------------------------------------------------------------------

    async def search_and_contents_async(self, query: str, **kwargs: Any) -> Any:
        if self._async is None:
            raise RuntimeError("AsyncExa client is not available in the installed exa_py SDK.")
        params = kwargs
        logger.debug("ExaAdapter.search_and_contents_async %s – params=%s", query, params)
        return await self._async.search_and_contents(query, **params)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_camel_case_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Convert *snake_case* keys in *kwargs* to *camelCase*.

        Example: ``start_published_date`` → ``startPublishedDate``.

        .. deprecated:: 0.0.0
            This method is no longer used and will be removed in a future version.
        """

        transformed: dict[str, Any] = {}
        for key, value in kwargs.items():
            if value is None:
                continue  # Skip unset optional params – avoids SDK validation errors.

            if "_" in key:
                parts = key.split("_")
                camel_key = parts[0] + "".join(word.capitalize() for word in parts[1:])
            else:
                camel_key = key
            transformed[camel_key] = value
        return transformed

    # -----------------------------------------------
    # Accessors
    # -----------------------------------------------
    @property
    def sync_client(self) -> Exa:
        """Expose the underlying synchronous SDK client (read-only)."""
        return self._sync
