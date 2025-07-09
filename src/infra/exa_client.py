import asyncio
import os
import random
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional, cast
from urllib.parse import urlparse

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .cached_search import CachedSearch
from .exa_adapter import ExaAdapter

# Attempt relative import for normal package use (e.g., when app.py imports this)
try:
    from ..config import config as app_config
    from ..models.paper import Paper  # Import Paper model
except ImportError:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if PROJECT_ROOT not in sys.path:
        sys.path.append(PROJECT_ROOT)
    from config import config as app_config  # type: ignore[no-redef]
    from models.paper import Paper  # type: ignore[no-redef]


# --- Custom Exa Client Exceptions ---
class ExaClientError(Exception):
    """Base exception for ExaClient errors."""

    pass


class ExaAPIError(ExaClientError):
    """Raised for general Exa API errors."""

    pass


class ExaRateLimitError(ExaAPIError):
    """Raised for Exa API rate limit errors."""

    pass


class ExaAuthenticationError(ExaClientError):
    """Raised for Exa API authentication errors."""

    pass


class ExaInvalidRequestError(ExaClientError):
    """Raised for invalid requests to the Exa API."""

    pass


class ExaClient:
    MAX_RETURNED_PAPERS: int = 50  # Allow a larger internal fetch pool (UI still displays ≤10)

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Exa API key is required.")
        # Raw SDK client kept for legacy uses but prefer adapter
        self.adapter = ExaAdapter(api_key=api_key)
        self.client = self.adapter.sync_client
        # Legacy caches (still used elsewhere)
        self._last_random_pool: list[Paper] = []
        self._last_top_k: list[Paper] = []
        self._last_display: list[Paper] = []
        self._last_random_paper: Optional[Paper] = None

        # New pooled search cache
        self._last_cached_search: Optional[CachedSearch] = None
        self._last_url_pinned_id: str | None = None

    # --- Helper functions -------------------------------------------------

    def _get_id_from_url(self, url: str) -> Optional[str]:
        """Return Exa document ID for a given URL.

        Uses a targeted search on the `url:"..."` field. Returns None if not found
        or if an exception occurs.
        """
        try:
            resp = self.client.search(
                f'url:"{url}"',
                num_results=1,
                use_autoprompt=False,
                category="research_paper",
            )
            if resp.results:
                return resp.results[0].id
        except Exception as e:
            logger.warning("_get_id_from_url failed for %s: %s", url, e, exc_info=True)
        return None

    # ------------------------------------------------------------------

    @staticmethod
    def _extract_arxiv_id(url_or_text: str | None) -> Optional[str]:
        """Extracts the bare arXiv identifier (e.g. '2006.09661') from a URL or text.

        Handles common variants such as:
        - https://arxiv.org/abs/2006.09661v1
        - https://arxiv.org/pdf/2006.09661.pdf
        - arXiv:2006.09661v2
        Returns None if no arXiv ID is found.
        """
        if not url_or_text:
            return None
        txt = str(url_or_text).strip()
        # Quick path – look for the 4-digit year style IDs first.
        # Regex roughly matches: YYMM.number[version]
        # Older formats with categories are ignored for now.
        match = re.search(r"(?P<id>\d{4}\.\d{4,5})(v\d+)?", txt)
        if match:
            return match.group("id")
        return None

    @staticmethod
    def _canonical_arxiv_abs_url(arxiv_id: str) -> str:
        """Returns the canonical arxiv.org/abs/ URL for a given arXiv id."""
        return f"https://arxiv.org/abs/{arxiv_id}"

    @staticmethod
    def _is_valid_paper(paper: Paper | None) -> bool:
        """Return True if the paper has minimally acceptable metadata.

        Currently we require:
        1. Title present and not the placeholder "No Title Provided".
        2. Title is at least 8 characters (heuristic to filter out junk like "N/A").
        3. URL present.
        4. At least one author or some text content.
        Additional criteria can be added later (e.g., authors populated).
        """
        if paper is None:
            return False
        title_clean = (paper.title or "").strip()
        if not title_clean or title_clean == "No Title Provided":
            return False
        if len(title_clean) < 8:
            return False
        # Blacklist certain keywords that indicate non-paper pages
        blacklist = ["recaptcha", "captcha", "not found", "404", "error"]
        lc_title = title_clean.lower()
        if any(bad in lc_title for bad in blacklist):
            return False
        if not paper.url:
            return False
        # Require some substantive metadata: at least one author or sufficient text content
        return bool(
            (paper.authors and len(paper.authors) > 0)
            or (paper.text_content and len(paper.text_content) >= 100)
        )

    # ---------------------------------------------------------------------

    def answer(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """Retrieve a short answer and supporting passages using Exa's /answer endpoint.

        Returns a dict with keys:
            - 'answer': str | None
            - 'passages': list[dict[str, str]]  # each: {'snippet': str, 'url': str}
        If Exa fails or no answer is found, returns an empty dict.
        """
        try:
            resp = self.adapter.answer(question, num_results=top_k)
            answer_text: str | None = getattr(resp, "answer", None)
            passages: list[dict[str, str]] = []
            if hasattr(resp, "results") and resp.results:
                for res in resp.results[:top_k]:
                    snippet = (
                        getattr(res, "text", "").strip() or getattr(res, "snippet", "").strip()
                    )
                    passages.append(
                        {
                            "snippet": snippet[:512],
                            "url": getattr(res, "url", "") or getattr(res, "id", ""),
                        }
                    )
            return {"answer": answer_text, "passages": passages}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ExaClient.answer failed for question '%s': %s", question, exc, exc_info=True
            )
            return {}

    # ---------------------------------------------------------------------

    def _create_paper_from_exa_result(
        self, result: Any, source_description: str
    ) -> Optional[Paper]:
        """Helper function to create a Paper object from an Exa result."""
        try:
            # --- DEBUG: Print the raw result object and its attributes ---
            logger.debug(
                f"--- Raw Exa Result (ID: {result.id or result.url}) for {source_description} ---"
            )
            # try:
            #     logger.debug(f"All attributes: {dir(result)}")
            #     if hasattr(result, 'author'): logger.debug(f"Raw author (singular): {result.author}")
            #     else: logger.debug("Raw author (singular): Not found")
            #     if hasattr(result, 'text'): logger.debug(f"Raw text (snippet): {result.text[:200]}...")
            #     else: logger.debug("Raw text: Not found")
            #     if hasattr(result, 'published_date'): logger.debug(f"Raw published_date: {result.published_date}")
            #     else: logger.debug("Raw published_date: Not found")
            # except Exception as e_print:
            #     logger.warning(f"Error logging result attributes: {e_print}")
            # logger.debug("--------------------------------------------------")
            # --- END DEBUG ---

            authors_list = []
            if hasattr(result, "author") and result.author:
                raw_author = str(result.author)
                # Skip if looks like submission metadata instead of authors
                if not raw_author.lower().startswith("[submitted"):
                    authors_list = (
                        [name.strip() for name in raw_author.split(",") if name.strip()]
                        if "," in raw_author
                        else [raw_author]
                    )

            text_content = None
            if hasattr(result, "text") and result.text:
                text_content = result.text
            elif hasattr(result, "summary") and result.summary:
                # Fallback to Exa's auto-summary if full text not returned
                text_content = result.summary

            published_date_str = getattr(result, "published_date", None)
            paper_published_date: datetime | None = None
            if published_date_str:
                try:
                    # Handle dates like '2023-11-20' or full ISO '2023-11-20T15:35:56Z'
                    if "T" in published_date_str:
                        dt_obj = datetime.fromisoformat(published_date_str.replace("Z", "+00:00"))
                    else:
                        dt_obj = datetime.strptime(published_date_str, "%Y-%m-%d")

                    # Ensure it's timezone-aware (assume UTC if not specified)
                    if dt_obj.tzinfo is None:
                        paper_published_date = dt_obj.replace(tzinfo=timezone.utc)
                    else:
                        paper_published_date = dt_obj

                except (ValueError, TypeError):
                    paper_published_date = None

            # Canonicalize URL (e.g. upgrade arXiv abs → PDF)
            canonical_url = self._upgrade_to_pdf_url(result.url)

            paper_data = {
                "id": result.id if result.id else canonical_url,
                "title": result.title if result.title else "No Title Provided",
                "url": canonical_url,
                "authors": authors_list,
                "text_content": text_content,
                "published_date": paper_published_date,
                "score": result.score if hasattr(result, "score") else None,
                "source": source_description,
            }
            return Paper(**paper_data)  # type: ignore[arg-type]
        except Exception as pydantic_error:
            logger.error(
                f"Error creating Paper object for result {result.id or result.url} from {source_description}: {pydantic_error}"
            )
            logger.error(f"Problematic data for Paper creation: {paper_data}")
            return None

    @retry(
        stop=stop_after_attempt(app_config.EXA_MAX_RETRIES),
        wait=wait_exponential(
            multiplier=1, min=app_config.EXA_RETRY_MIN_WAIT, max=app_config.EXA_RETRY_MAX_WAIT
        ),
        retry=retry_if_exception_type((ExaAPIError, ExaRateLimitError)),
        retry_error_callback=lambda retry_state: [],
    )
    def search_papers(
        self,
        query: str,
        num_results: int = 8,
        *,
        start_published_date: datetime | None = None,
        end_published_date: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        include_text_list: list[str] | None = None,
        exclude_text_list: list[str] | None = None,
        authors: list[str] | None = None,
        category: str = "research_paper",
        use_autoprompt: bool = True,
    ) -> list[Paper]:
        """
        Searches for research papers using the Exa API and returns them as Paper objects.

        Args:
            query: The search query string (e.g., a research topic).
            num_results: The desired number of search results (will be capped at MAX_RETURNED_PAPERS).
            start_published_date: Optional start date for filtering results.
            end_published_date: Optional end date for filtering results.
            include_domains: Optional list of domains to include in results.
            exclude_domains: Optional list of domains to exclude from results.
            include_text_list: Optional list of text to include in results.
            exclude_text_list: Optional list of text to exclude from results.
            authors: Optional list of authors to filter results by.
            category: The category of content to search for.
            use_autoprompt: Whether to let Exa optimize the query.

        Returns:
            A list of Paper Pydantic model instances.
        """
        # Enforce upper bound for UI sanity.
        desired_count = min(num_results, self.MAX_RETURNED_PAPERS)

        # Heuristic: request more than needed to allow for filtering. 3× desired but max 20.
        request_count = min(desired_count * 3, 20)

        # Prepare date cutoff for local filtering (Exa may ignore it).
        cutoff_dt: datetime | None = start_published_date
        apply_filters = os.getenv("EXA_NO_FILTERS", "0") != "1"

        papers: list[Paper] = []
        try:
            # Build optional filter kwargs
            exa_kwargs: dict[str, Any] = {
                "num_results": request_count,
                "use_autoprompt": use_autoprompt,
                "type": "neural",  # Always use Exa's neural ranking for best semantic relevance.
                "category": category,
            }

            if start_published_date:
                exa_kwargs["start_published_date"] = start_published_date.strftime("%Y-%m-%d")
            if end_published_date:
                exa_kwargs["end_published_date"] = end_published_date
            if include_domains:
                exa_kwargs["include_domains"] = include_domains
            if exclude_domains:
                exa_kwargs["exclude_domains"] = exclude_domains
            if include_text_list:
                exa_kwargs["include_text_list"] = include_text_list
            if exclude_text_list:
                exa_kwargs["exclude_text_list"] = exclude_text_list

            # If author filters specified, weave them into the query string
            query_to_send = query
            if authors:
                cleaned_authors: list[str] = [a.strip() for a in authors if a.strip()]
                if cleaned_authors:
                    author_fragments = " ".join([f'author:"{a}"' for a in cleaned_authors])
                    query_to_send = f"{query} {author_fragments}"

            # Use search_and_contents to get results and their full text in one call
            search_response = self.adapter.search_and_contents(
                query_to_send,
                **exa_kwargs,
            )

            # --- DEBUG: Log all raw Exa results before client-side filtering ---
            try:
                logger.debug(
                    "Raw Exa results (pre-filter): [{}]",
                    ", ".join(
                        f"{getattr(r, 'title', 'NO TITLE')} | {getattr(r, 'published_date', 'no-date')} | score={getattr(r, 'score', 'NA')}"
                        for r in search_response.results
                    ),
                )
            except Exception:
                # Ensure logging issues never break search flow
                logger.exception("Failed to log raw Exa results")

            # The structure of search_response.results from search_and_contents might be directly usable.
            # Each item in search_response.results should now have a .text field with the full content.
            # It should also hopefully have .author, .id, .title, .url, .published_date.

            for result in search_response.results:
                paper = self._create_paper_from_exa_result(result, "Exa (search_and_contents)")
                if apply_filters:
                    if paper is None or not self._is_valid_paper(paper):
                        continue
                    # Domain filter
                    if not self._domain_allowed(
                        paper.url,
                        include_domains=include_domains,
                        exclude_domains=exclude_domains,
                    ):
                        continue
                    # Local date cutoff check
                    if cutoff_dt and paper.published_date:
                        try:
                            # paper.published_date is now guaranteed to be tz-aware from _create_paper_from_exa_result
                            if (
                                isinstance(paper.published_date, datetime)
                                and paper.published_date < cutoff_dt
                            ):
                                continue
                        except Exception:
                            # If date unparseable or other error, drop
                            continue
                else:
                    # Even when not applying filters, skip if Paper creation failed
                    if paper is None:
                        continue
                papers.append(paper)

            # Ensure we return exactly desired_count papers while injecting a serendipity pick.
            if len(papers) >= desired_count:
                papers_sorted = sorted(
                    papers,
                    key=lambda p: (p.score if p.score is not None else 0.0),
                    reverse=True,
                )
                self._last_top_k = papers_sorted[:desired_count]
                main_results = papers_sorted[: max(desired_count - 1, 0)]
                random_pool = papers_sorted[desired_count - 1 :]

                selected_random: list[Paper] = []
                if random_pool:
                    random_paper = random.choice(random_pool)
                    selected_random.append(random_paper)
                    # Cache pool for potential future shuffle requests
                    self._last_random_pool = random_pool
                    self._last_random_paper = random_paper

                final_results = (main_results + selected_random)[:desired_count]
                # Cache display list for reset/shuffle-all endpoints
                self._last_display = final_results
                return final_results
            # Fallback – not enough papers to build a pool
            return papers[:desired_count]
        except Exception as e:
            logger.error(f"Error during Exa API search or paper model creation: {e}", exc_info=True)
            # Raise wrapped error to trigger tenacity retry; after retries are exhausted,
            # retry_error_callback will convert this into a safe empty list, preserving
            # previous non-breaking behaviour.
            raise ExaAPIError(str(e)) from e

        # ------------------------------------------------------------------

    # Cache access helpers
    # ------------------------------------------------------------------

    def get_last_top_k(self) -> list["Paper"]:
        """Return the most recent top-k list (original order)."""
        return list(self._last_top_k)

    def get_last_display_list(self) -> list["Paper"]:
        """Return the list that was actually sent to the frontend (top-k + wildcard)."""
        return list(self._last_display)

    def get_last_random_paper(self) -> Optional["Paper"]:
        """Return the last random (wild-card) paper that was injected, if any."""
        return self._last_random_paper

    def get_last_full_pool(self) -> list["Paper"]:
        """Return the full pool of cached papers from the last search.

        Combines the original top-k list and the tail pool used for wild-card picks.
        Safe to call even if no search has been run (returns empty list).
        """
        return list(self._last_top_k) + list(self._last_random_pool)

    # ------------------------------------------------------------------
    # Serendipity helpers
    # ------------------------------------------------------------------

    def get_random_from_last_pool(self) -> Optional["Paper"]:
        """Return a random paper from the most recent search’s tail pool.

        Returns ``None`` if no pool is cached (e.g. before first search or if the
        previous search returned too few papers).
        """
        if not self._last_random_pool:
            return None
        return random.choice(self._last_random_pool)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def is_url(self, text: str) -> bool:
        """
        Check if the given text is a URL, with a preference for academic-like URLs.

        Args:
            text: The text to check.

        Returns:
            True if the text appears to be a plausible paper URL, False otherwise.
        """
        # Stricter: requires http/https, and either a known academic domain
        # or a path with at least one segment for general domains.
        url_pattern = re.compile(
            r"^(https?:\/\/)"  # Must have http:// or https://
            r"("
            r"([a-zA-Z0-9-]+\.)*(arxiv\.org|biorxiv\.org|medrxiv\.org|doi\.org|acm\.org|ieee\.org|springer\.com|elsevier\.com|wiley\.com|nature\.com|sciencemag\.org|pubmed\.ncbi\.nlm\.nih\.gov|ssrn\.com|semanticscholar\.org|researchgate\.net)"  # Known scholarly domains
            r"(\/[\S]*)?"  # Optional path for these domains
            r"|"
            r"([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,6}"  # General domain
            r"(\/([\w\.-]+)){1,}\/?"  # Must have at least one path segment for general domains
            r"([?#]\S*)?"  # Optional query or fragment
            r")$",
            re.IGNORECASE,
        )
        return bool(url_pattern.match(text.strip()))

    @retry(
        stop=stop_after_attempt(app_config.EXA_MAX_RETRIES),
        wait=wait_exponential(
            multiplier=1, min=app_config.EXA_RETRY_MIN_WAIT, max=app_config.EXA_RETRY_MAX_WAIT
        ),
        retry=retry_if_exception_type((ExaAPIError, ExaRateLimitError)),
        retry_error_callback=lambda rs: [],
    )
    def find_similar_papers(
        self,
        paper_url: str,
        num_results: int = 5,
        category: str = "research_paper",
        *,
        start_published_date: datetime | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[Paper]:
        """
        Find papers similar to the one at the given URL using Exa's find_similar_and_contents endpoint.

        Args:
            paper_url: URL of the paper to find similar papers for.
            num_results: Number of similar papers to return.
            category: The category of content to search for.
            start_published_date: Optional start date for filtering results.
            include_domains: Optional list of domains to include in results.
            exclude_domains: Optional list of domains to exclude from results.

        Returns:
            A list of Paper objects representing similar papers.
        """
        desired_count = min(num_results, self.MAX_RETURNED_PAPERS)
        request_count = min(desired_count * 3, 20)

        # The find_similar API does not support server-side date filters. If
        # callers need date constraints, they should post-filter the returned
        # list.  For now we skip any local parsing to avoid unused-variable
        # lint errors.

        papers: list[Paper] = []
        try:
            # Build optional filter kwargs
            exa_kwargs: dict[str, Any] = {
                "num_results": request_count,
                "category": category,
            }

            if start_published_date:
                exa_kwargs["start_published_date"] = start_published_date.strftime("%Y-%m-%d")
            if include_domains:
                exa_kwargs["include_domains"] = include_domains
            if exclude_domains:
                exa_kwargs["exclude_domains"] = exclude_domains

            # Use find_similar_and_contents to get similar papers and their content
            similar_response = self.client.find_similar_and_contents(
                paper_url,
                **exa_kwargs,
                text=True,
            )

            # --- DEBUG: Log raw similar results before filtering ---
            try:
                logger.debug(
                    "Raw Exa similar results (pre-filter): [{}]",
                    ", ".join(
                        f"{getattr(r, 'title', 'NO TITLE')} | {getattr(r, 'published_date', 'no-date')} | score={getattr(r, 'score', 'NA')}"
                        for r in similar_response.results
                    ),
                )
            except Exception:
                logger.exception("Failed to log raw Exa similar results")

            # Process the response similar to search_papers
            for result in similar_response.results:
                paper = self._create_paper_from_exa_result(
                    result,
                    f"Exa (similar to: {paper_url})",
                )

                # Skip invalid or None papers early (narrows type for mypy)
                if paper is None or not self._is_valid_paper(paper):
                    continue

                # Apply domain filter (include/exclude not passed here yet)
                if not self._domain_allowed(
                    paper.url,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                ):
                    continue

                if (
                    start_published_date
                    and paper.published_date
                    and isinstance(paper.published_date, datetime)
                    and paper.published_date < start_published_date
                ):
                    continue

                papers.append(paper)

            # Keep the k highest-score similar papers
            if len(papers) > desired_count:
                papers_sorted = sorted(
                    papers,
                    key=lambda p: (p.score if p.score is not None else 0.0),
                    reverse=True,
                )
                return papers_sorted[:desired_count]
            return papers
        except Exception as e:
            logger.error(f"Error during Exa API similar papers search: {e}", exc_info=True)
            raise ExaAPIError(str(e)) from e

    def get_paper_details_and_similar_from_url(
        self,
        paper_url: str,
        num_similar_results: int = 5,
        *,
        category: str = "research_paper",
        start_published_date: datetime | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Fetches details for a given paper URL and finds similar papers.

        Args:
            paper_url: URL of the paper to fetch and find similars for.
            num_similar_results: Number of similar papers to return.
            category: The category of content for similar papers search.
            start_published_date: Optional start date for filtering results.
            include_domains: Optional list of domains to include in results.
            exclude_domains: Optional list of domains to exclude from results.

        Returns:
            A dictionary with 'main_paper' (Paper object or None) and
            'similar_papers' (List of Paper objects).
        """
        main_paper_object: Optional[Paper] = None
        similar_papers_list: list[Paper] = []

        # 1. Fetch content for the main paper_url
        try:
            logger.info(f"Fetching content for main URL: {paper_url}")
            # Precompute arXiv identifier (if any) from the *input* URL so we can
            # validate that the fetched result corresponds to the same paper.
            input_arxiv_id = self._extract_arxiv_id(paper_url)

            # 1) Crawl the *exact* URL (or its canonicalised PDF form) first so we
            #    don't accidentally fetch a different document via id look-up.
            canonical_url = self._upgrade_to_pdf_url(paper_url) or paper_url
            contents_response = self.client.get_contents([canonical_url], text=True)

            # 2) Fallback – if Exa had no content yet, try resolving the doc-id
            #    using a strict url:"<url>" search and crawl that id.
            if (not contents_response.results) or len(contents_response.results) == 0:
                doc_id = self._get_id_from_url(paper_url)
                if doc_id:
                    contents_response = self.client.get_contents([doc_id], text=True)

            if contents_response.results and len(contents_response.results) > 0:
                main_paper_result = contents_response.results[0]
                main_paper_object = self._create_paper_from_exa_result(
                    main_paper_result, f"Exa (get_contents: {paper_url})"
                )

                # ------------------------------------------------------------------
                # Sanity-check: make sure Exa didn't return a *different* arXiv paper
                # (this can happen when url:"..." search loosens matching and returns
                #  a related document).  If the extracted arXiv id from the fetched
                #  result does not match the one from the user's URL, discard it so
                #  that we don't pin an unrelated paper.
                # ------------------------------------------------------------------
                if main_paper_object and main_paper_object.url:
                    # Sanity check A – arXiv id match (if applicable)
                    if input_arxiv_id:
                        result_arxiv_id = self._extract_arxiv_id(str(main_paper_object.url))
                        if result_arxiv_id and result_arxiv_id != input_arxiv_id:
                            logger.warning(
                                "Discarding mismatched main paper. Expected arXiv id %s but got %s",
                                input_arxiv_id,
                                result_arxiv_id,
                            )
                            main_paper_object = None
                    # Sanity check B – same registered domain as original URL
                    if main_paper_object and main_paper_object.url:
                        orig_host = urlparse(paper_url).netloc.lower()
                        result_host = urlparse(str(main_paper_object.url)).netloc.lower()
                        if (
                            orig_host
                            and result_host
                            and orig_host.split(":")[0] != result_host.split(":")[0]
                        ):
                            logger.warning(
                                "Discarding mismatched main paper – host differs (%s → %s)",
                                orig_host,
                                result_host,
                            )
                            main_paper_object = None
            else:
                logger.warning(f"No content returned by Exa's get_contents for URL: {paper_url}")

            # Enrich main paper with search metadata if important fields are missing
            if main_paper_object and (
                (not main_paper_object.title or main_paper_object.title == "No Title Provided")
                or not main_paper_object.authors
                or not main_paper_object.published_date
            ):
                # 1) Try canonical arXiv abs URL if applicable
                arxiv_id = self._extract_arxiv_id(paper_url)
                if arxiv_id:
                    canonical_url = self._canonical_arxiv_abs_url(arxiv_id)
                    if canonical_url != paper_url:
                        try:
                            logger.info(
                                f"Attempting enrichment via canonical arXiv URL: {canonical_url}"
                            )
                            canonical_resp = self.client.get_contents([canonical_url], text=True)
                            if canonical_resp.results:
                                enriched_res = canonical_resp.results[0]
                                enriched_paper = self._create_paper_from_exa_result(
                                    enriched_res, f"Exa (get_contents: {canonical_url})"
                                )
                                if enriched_paper:
                                    if (
                                        enriched_paper.title
                                        and enriched_paper.title != "No Title Provided"
                                    ):
                                        main_paper_object.title = enriched_paper.title
                                    if enriched_paper.authors:
                                        main_paper_object.authors = enriched_paper.authors
                                    if enriched_paper.published_date:
                                        main_paper_object.published_date = (
                                            enriched_paper.published_date
                                        )
                        except Exception as e_canonical:
                            logger.warning(
                                f"Canonical arXiv enrichment failed: {e_canonical}", exc_info=True
                            )
                # 2) Final fallback – keyword search using bare arXiv id (if available) or original URL
                if arxiv_id is None:  # Only run keyword search for non-arXiv URLs
                    try:
                        search_query = paper_url
                        logger.info(
                            "Enriching main paper metadata via keyword search (non-arXiv URL): %s",
                            search_query,
                        )
                        enrich_results = self.search_papers(
                            query=search_query,
                            num_results=1,
                            use_autoprompt=False,
                        )
                        if enrich_results:
                            enriched = enrich_results[0]
                            if enriched.title and enriched.title != "No Title Provided":
                                main_paper_object.title = enriched.title
                            if enriched.authors:
                                main_paper_object.authors = enriched.authors
                            if enriched.published_date:
                                main_paper_object.published_date = enriched.published_date
                            if enriched.text_content:
                                main_paper_object.text_content = enriched.text_content
                            if enriched.score is not None:
                                main_paper_object.score = enriched.score
                    except Exception as e_enrich:
                        logger.warning(
                            "Could not enrich main paper metadata via keyword search: %s", e_enrich
                        )
        except Exception as e:
            logger.error(
                f"Error fetching content for main URL {paper_url} with Exa: {e}", exc_info=True
            )
            # main_paper_object remains None

        # 2. Find similar papers to the paper_url
        try:
            logger.info("Finding similar papers for URL: %s", paper_url)

            similar_papers_list = self.find_similar_papers(
                paper_url,
                num_results=num_similar_results,
                category=category,
                start_published_date=start_published_date,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        except Exception as e:
            logger.error(
                f"Error finding similar papers for URL {paper_url} with Exa: {e}", exc_info=True
            )
            # similar_papers_list remains empty or partially filled

        return {"main_paper": main_paper_object, "similar_papers": similar_papers_list}

    # ------------------------------------------------------------------
    # New pooled-search API (Phase-C)
    # ------------------------------------------------------------------

    def _fetch_papers_from_exa(self, query: str, k: int, **kwargs: Any) -> list["Paper"]:
        """Fetches a pool of papers from the Exa API."""
        request_count = min(k * 10, 50)
        logger.info(f"Fetching {request_count} papers from Exa for query: '{query}'")
        return self.search_papers(query, num_results=request_count, **kwargs)

    def _build_cache(self, papers: list["Paper"], k: int, query: str) -> CachedSearch | None:
        """Builds and caches the search results."""
        if not papers:
            return None

        # Build dict keyed by id (skip papers without id)
        paper_by_id = {p.id: p for p in papers if p.id}
        if not paper_by_id:
            return None

        # ------------------------------------------------------
        # Verbose logging of the cached pool for observability
        # ------------------------------------------------------
        logger.info(
            "_build_cache: post-filter pool created",
            query=query,
            k_requested=k,
            pool_size=len(paper_by_id),
            ids=list(paper_by_id.keys()),
        )

        orig_ids = list(paper_by_id.keys())[:k]
        cached = CachedSearch(paper_by_id=paper_by_id, k=k, orig_ids=orig_ids)
        cached.init_display()
        return cached

    def search_and_cache(self, query: str, k: int = 10, **kwargs: Any) -> list["Paper"]:
        """Fetches papers from Exa, caches them, and returns the initial display list."""
        papers = self._fetch_papers_from_exa(query, k, **kwargs)

        cached_search = self._build_cache(papers, k, query)
        self._last_cached_search = cached_search

        if not cached_search:
            return []

        return cached_search.get_display_papers()

    def url_search_and_cache(
        self,
        paper_url: str,
        *,
        k: int = 8,
        **kwargs: Any,
    ) -> list[Paper]:
        """Fetch *paper_url* and up to *k* similar papers, then cache.

        The input paper is treated as *pinned* and will always occupy
        position 0 in UI lists.  Shuffle / wildcard act on the remaining
        slots.
        """
        # Fetch details and similar papers (larger pool for caching)
        request_count = min(k * 5, 50)
        url_results = self.get_paper_details_and_similar_from_url(
            paper_url,
            num_similar_results=request_count,
            category=kwargs.get("category", "research_paper"),
            start_published_date=kwargs.get("start_published_date"),
            include_domains=kwargs.get("include_domains"),
            exclude_domains=kwargs.get("exclude_domains"),
        )
        main_paper = url_results.get("main_paper")
        similar_papers: list[Paper] = url_results.get("similar_papers", [])

        if not main_paper:
            # Fallback: if main paper failed, we just behave like a normal search on similars
            papers_pool = similar_papers
            pinned_id: str | None = None
        else:
            papers_pool = [main_paper] + similar_papers

            # --------------------------------------------------
            # Kick off background full-text extraction
            # --------------------------------------------------
            try:
                from infra.text_extractor import TextExtractor

                asyncio.create_task(
                    TextExtractor.instance().ensure_cached(main_paper.url, main_paper.id)
                )
            except Exception as bg_exc:  # noqa: BLE001 – background should never break flow
                logger.debug("TextExtractor background task failed to start: %s", bg_exc)
            pinned_id = main_paper.id if main_paper.id else None

        # Build dict keyed by id (skip items without id)
        paper_by_id = {p.id: p for p in papers_pool if p.id}
        if not paper_by_id:
            self._last_cached_search = None
            return []

        # Make sure pinned id is in dict
        if pinned_id and pinned_id not in paper_by_id and main_paper and main_paper.id:
            paper_by_id[main_paper.id] = main_paper

        # orig_ids: input paper first (if any) then top-k similar order
        orig_ids: list[str] = []
        if pinned_id:
            orig_ids.append(pinned_id)
        # Append other ids in their existing order, excluding pinned and ensuring max pool size
        for p in papers_pool:
            if p.id and p.id != pinned_id:
                orig_ids.append(p.id)
        # Ensure k length
        orig_ids = orig_ids[:k]

        cached = CachedSearch(
            paper_by_id=paper_by_id,
            k=k,
            orig_ids=orig_ids,
            pinned_id=pinned_id,
        )
        cached.init_display()
        self._last_cached_search = cached
        self._last_url_pinned_id = pinned_id
        return cached.get_display_papers()

    # Convenience getters used by API routes

    def get_cached_search(self) -> CachedSearch | None:
        return self._last_cached_search

    def replace_wildcard(self) -> Optional["Paper"]:
        cs = self._last_cached_search
        if cs is None:
            return None
        return cast(Optional["Paper"], cs.replace_wildcard())

    def shuffle_k(self) -> list["Paper"]:
        cs = self._last_cached_search
        return cs.shuffle_k() if cs is not None else []

    def reset_display(self) -> list["Paper"]:
        cs = self._last_cached_search
        return cs.reset_display() if cs is not None else []

    # ---------------- Domain filter helper ---------------------------

    @staticmethod
    def _domain_allowed(
        url: Any | None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> bool:
        """Return ``True`` if *url* satisfies the domain constraints.

        * include_domains – at least one must match if provided.
        * exclude_domains – none may match.

        Matching is done against the *registered host* (netloc) of the URL,
        converted to lowercase. A simple ``endswith`` check is used so that a
        filter of ``arxiv.org`` matches ``www.arxiv.org`` as well.
        """
        if url is None:
            return False

        # Accept str, Pydantic HttpUrl, or any object implementing __str__.
        url_str = str(url)

        try:
            host = urlparse(url_str).netloc.lower()
        except Exception:  # pragma: no cover – broken URLs
            host = url_str.lower()

        # Quick synonym expansion for popular domains
        synonyms: dict[str, list[str]] = {
            "arxiv.org": ["ar5iv.org"],
        }

        def _expand(domain: str) -> list[str]:
            domain_lc = domain.lower()
            return [domain_lc] + synonyms.get(domain_lc, [])

        # Include filter -------------------------------------------------
        if include_domains:
            allowed = False
            for d in include_domains:
                for pattern in _expand(d):
                    if host.endswith(pattern):
                        allowed = True
                        break
                if allowed:
                    break
            if not allowed:
                return False

        # Exclude filter -------------------------------------------------
        if exclude_domains:
            for d in exclude_domains:
                for pattern in _expand(d):
                    if host.endswith(pattern):
                        return False

        return True

    # ------------------------------------------------------------------
    # Canonical URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _upgrade_to_pdf_url(url: str | None) -> str | None:
        """Return a more reliable *PDF* URL when possible.

        Currently supports:
        • arXiv / ar5iv: converts any *abs* or generic link into
          https://arxiv.org/pdf/<id>.pdf so that Exa (and our downstream
          content fetchers) can obtain the full paper text more reliably.

        If the input *url* is already a direct PDF link (ends with '.pdf') or
        the domain is not recognised, the original URL is returned unchanged.
        """
        if not url:
            return url

        url_str = str(url)

        # Quick exit if already a PDF
        if url_str.lower().endswith(".pdf"):
            return url_str

        if "arxiv.org" in url_str or "ar5iv.org" in url_str:
            arxiv_id = ExaClient._extract_arxiv_id(url_str)
            if arxiv_id:
                return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        # OpenReview – convert forum or landing page URLs to the canonical PDF endpoint
        if "openreview.net" in url_str:
            parsed = urlparse(url_str)
            paper_id = None
            # Pattern: /forum?id=<id>
            if parsed.query:
                for kv in parsed.query.split("&"):
                    if kv.startswith("id="):
                        paper_id = kv.split("=", 1)[1]
                        break
            # Pattern: /forum/<id>
            if not paper_id and parsed.path.startswith("/forum"):
                parts = parsed.path.rstrip("/").split("/")
                if len(parts) >= 3:
                    paper_id = parts[-1]
            if paper_id:
                return f"https://openreview.net/pdf?id={paper_id}"

        # ACL Anthology (aclanthology.org / aclweb.org) – append .pdf if missing
        if "aclanthology.org" in url_str or "aclweb.org" in url_str:
            parsed = urlparse(url_str)
            # Already direct PDF link
            if parsed.path.lower().endswith(".pdf"):
                return url_str
            clean_path = parsed.path.rstrip("/")
            if clean_path:
                return f"https://{parsed.netloc}{clean_path}.pdf"

        # TODO: Add more domain-specific handlers (e.g. biorxiv) as needed.
        return url_str


# Example Usage (for direct testing of this module):
if __name__ == "__main__":
    # Configure basic logging for script execution
    logger.remove(0)  # Remove default handler
    logger.add(
        sys.stderr, level="INFO", format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    if not app_config.EXA_API_KEY:
        logger.error("EXA_API_KEY not found in environment. Please set it in your .env file.")
    else:
        logger.info(f"Using EXA_API_KEY: {app_config.EXA_API_KEY[:5]}...")
        client = ExaClient(api_key=app_config.EXA_API_KEY)

        # Test search_papers
        test_query = "applications of AI in drug discovery"
        logger.info(f"\n--- Searching for: '{test_query}' ---")
        search_results = client.search_papers(test_query, num_results=2)
        if search_results:
            for paper in search_results:
                logger.info(f"  Title: {paper.title}")
                logger.info(f"  URL: {paper.url}")
                logger.info(f"  Authors: {paper.authors}")
                logger.info(f"  Published: {paper.published_date}")
                logger.info(
                    f"  Abstract snippet: {getattr(paper, 'abstract', '')[:200] if getattr(paper, 'abstract', None) else 'N/A'}..."
                )
                logger.info("  ----")
        else:
            logger.info("No search results found.")

        # Test find_similar_papers (using a known URL, replace if needed)
        # Ensure the URL is one that is likely to have similar papers and content
        # For example, a well-known arXiv paper URL
        # test_url_for_similar = "https://arxiv.org/abs/1706.03762" # Attention is All You Need
        # Using a more recent example that might be in Exa's index with content
        test_url_for_similar = "https://arxiv.org/abs/2307.09288"  # Llama 2 paper, adjust if needed
        logger.info(f"\n--- Finding similar papers to: '{test_url_for_similar}' ---")
        similar_papers_results = client.find_similar_papers(test_url_for_similar, num_results=2)
        if similar_papers_results:
            for paper in similar_papers_results:
                logger.info(f"  Title: {paper.title}")
                logger.info(f"  URL: {paper.url}")
                logger.info(f"  Authors: {paper.authors}")
                logger.info(f"  Published: {paper.published_date}")
                logger.info(
                    f"  Content snippet: {getattr(paper, 'abstract', '')[:200] if getattr(paper, 'abstract', None) else 'N/A'}..."
                )  # Assuming abstract now holds full content
                logger.info("  ----")
        else:
            logger.info("No similar papers found or error in fetching.")

        # Test is_url
        test_url = "https://example.com/path/to/page"
        test_query_text = "this is a normal query"
        logger.info("\n--- URL Check ---")
        logger.info(f"Is '{test_url}' a URL? {client.is_url(test_url)}")
        logger.info(f"Is '{test_query_text}' a URL? {client.is_url(test_query_text)}")
