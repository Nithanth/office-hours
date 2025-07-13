"""Thin wrapper around *exa_py* with stable snake_case API and structured logging."""

from typing import Any, Final

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    # The canonical synchronous & async clients shipped by exa_py.
    from exa_py import Exa
except ModuleNotFoundError as exc:  # pragma: no cover - dev mis-config
    raise ImportError(
        "The 'exa_py' package is required but not installed. Add it to your "
        "environment or requirements.txt."
    ) from exc


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
    def search_and_contents(self, query: str, **kwargs: Any) -> Any:  # noqa: ANN401 - SDK types
        """Wrapper for :py:meth:`exa_py.Exa.search_and_contents`.

        Parameters are accepted in *snake_case* and passed directly to the SDK.
        """

        # Exa SDK expects snake_case parameter names. Passing kwargs directly
        # avoids validation errors such as "Invalid option: 'numResults'".
        params = kwargs
        logger.debug("Exa search_and_contents", query=query, params=params)
        return self._sync.search_and_contents(query, **params)

    # ------------------------------------------------------------------
    # Answer endpoint - short answers with supporting passages
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def answer(self, question: str, **kwargs: Any) -> Any:  # noqa: ANN401 - SDK types
        """Wrapper for :py:meth:`exa_py.Exa.answer`.

        Returns Exa SDK *Answer* object which contains an `answer` string and
        supporting `results` list.
        """
        logger.debug("Exa answer", question=question, params=kwargs)
        return self._sync.answer(question, **kwargs)

    # ------------------------------------------------------------------
    # Async variant - handy for future performance improvements.
    # ------------------------------------------------------------------

    async def search_and_contents_async(self, query: str, **kwargs: Any) -> Any:
        if self._async is None:
            raise RuntimeError("AsyncExa client is not available in the installed exa_py SDK.")
        params = kwargs
        logger.debug("Exa search_and_contents_async", query=query, params=params)
        return await self._async.search_and_contents(query, **params)

    # -----------------------------------------------
    # Accessors
    # -----------------------------------------------
    @property
    def sync_client(self) -> Exa:
        """Expose the underlying synchronous SDK client (read-only)."""
        return self._sync
