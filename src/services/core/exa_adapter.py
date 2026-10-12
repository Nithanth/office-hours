"""Thin wrapper around *exa_py* 2.x with stable snake_case API and structured logging."""

from typing import Any, Final, cast

from loguru import logger
from tenacity import (
    retry,
    retry_base,
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


def _is_retryable_http_error(exc: BaseException) -> bool:
    """Return True for HTTP 429 (rate-limit) or 5xx (server) errors."""
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = cast(int, exc.response.status_code)
            return status_code == 429 or status_code >= 500
    except ImportError:
        pass
    return False


class _RetryTransient(retry_base):
    """Retry on connection/timeout errors and HTTP 429/5xx."""

    def __call__(self, retry_state: Any) -> bool:  # noqa: ANN401
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc is None:
            return False
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        return _is_retryable_http_error(exc)


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

    _RETRY_POLICY: Final = _RetryTransient()

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Exa API key is required to instantiate ExaAdapter.")
        self._sync: Exa = Exa(api_key=api_key)

    # ---------------------------------------------------------------------
    # Search (replaces search_and_contents in 2.x)
    # ---------------------------------------------------------------------

    @retry(
        retry=_RETRY_POLICY,
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
        retry=_RETRY_POLICY,
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def find_similar(self, url: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Find pages similar to *url*.

        ``Exa.find_similar`` is deprecated in exa-py 2.x; per the SDK's
        migration note this calls ``search("pages similar to " + url)``
        with the same filters.
        """
        kwargs.pop("use_autoprompt", None)
        query = f"pages similar to {url}"
        logger.debug("Exa find_similar", url=url, params=kwargs)
        if _METRICS_ENABLED and EXA_LAT is not None:
            with EXA_LAT.time():
                return self._sync.search(query, **kwargs)
        return self._sync.search(query, **kwargs)

    # ------------------------------------------------------------------
    # Get contents (still supported in 2.x)
    # ------------------------------------------------------------------

    @retry(
        retry=_RETRY_POLICY,
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
        retry=_RETRY_POLICY,
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def answer(self, question: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Short answer with supporting passages via :py:meth:`exa_py.Exa.answer`.

        Note: v2 answer() does NOT accept num_results; unsupported kwargs are stripped.
        """
        # Strip params not supported by v2 answer()
        kwargs.pop("num_results", None)
        kwargs.pop("use_autoprompt", None)
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
