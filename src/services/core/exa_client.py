"""High-level Exa search/orchestration service.

This module bridges Lux Scholar functionality with the official `exa_py` SDK.
It converts high-level application workflows (search, similarity, answer, and
enrichment) into concrete SDK calls, adds local caching & retry logic, and
maps Exa SDK results into first-class `Paper` domain objects.  Pure stateless
helpers live in `services.core.exa_utils`; any orchestration that requires
network access or shared caches remains here.
"""

import asyncio
import re
import time
from collections import Counter

# Shared thread pool for enrich_url calls (avoids creating a new pool per call)
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from datetime import datetime
from typing import Any, Optional, cast

from loguru import logger

import config as _cfg
from models.paper import Paper
from services.paper.cache.paper_cache import PaperCache

# ---------------------------------------------------------------------------
# Metadata fetch helpers (moved to services.paper.metadata)
# ---------------------------------------------------------------------------
from services.paper.metadata.metadata_fetchers import (
    fetch_arxiv_metadata as _fetch_arxiv_metadata,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_arxiv_metadata_batch as _fetch_arxiv_batch,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_crossref_metadata as _fetch_crossref_metadata,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_doi_metadata as _fetch_doi_metadata,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_semantic_scholar_arxiv as _fetch_ss_arxiv,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_semantic_scholar_arxiv_batch as _fetch_ss_arxiv_batch,
)
from services.query.cache import CachedSearch

from .exa_adapter import ExaAdapter
from .exa_utils import (
    author_utils as _author_utils,
)
from .exa_utils import (
    filter_utils as _filter_utils,
)
from .exa_utils import (
    paper_utils as _paper_utils,
)
from .exa_utils import (
    url_utils as _url_utils,
)

_ENRICH_POOL = _ThreadPoolExecutor(max_workers=4)


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
    """Lightweight orchestration layer around the Exa Python SDK.

    This class converts high-level application workflows (e.g. *search*,
    *find similar*, *answer*, *enrich*) into concrete Exa SDK calls, adds local
    caching & concurrency, and maps raw SDK objects into domain-level
    :class:`~models.paper.Paper` instances.

    Pure, stateless helpers live in ``infra.exa_utils``; any stateful orchestration
    logic that needs to coordinate network calls or caching remains here.
    """

    # Upper bound on the raw result pool fetched from Exa before local filtering.
    # Keeping this modest is the single biggest win for latency.  Tunable via env.
    MAX_RETURNED_PAPERS: int = _cfg.config.EXA_POOL_SIZE

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Exa API key is required.")
        # Raw SDK client kept for legacy uses but prefer adapter
        self.adapter = ExaAdapter(api_key=api_key)
        self.client = self.adapter.sync_client
        # Legacy per-request cache objects are now handled via CachedSearch.
        # SQLite paper metadata cache (no external service)
        self._cache = PaperCache()

        # Per-session pooled search cache keyed by session_id.
        # Avoids race conditions when multiple users search concurrently.
        self._cached_searches: dict[str, CachedSearch] = {}
        self._url_pinned_ids: dict[str, str | None] = {}

    def _get_id_from_url(self, url: str) -> Optional[str]:
        """Return Exa document ID for a given URL.

        Uses a targeted search on the `url:"..."` field. Returns None if not found
        or if an exception occurs.
        """
        try:
            resp = self.client.search(
                f'url:"{url}"',
                num_results=1,
                category="research paper",
                contents=False,
            )
            if resp.results:
                return resp.results[0].id
        except Exception as e:
            logger.warning("_get_id_from_url failed for {}: {}", url, e, exc_info=True)
        return None

    def _safe_get_contents(self, url: str) -> Any | None:
        """Best-effort wrapper around `get_contents`.

        Returns the first result from Exa if available. Only falls back to a
        search-by-URL lookup when the primary call returns no results at all,
        avoiding unnecessary extra API calls.
        """
        try:
            res = self.client.get_contents([url], text=True)
            if getattr(res, "results", None):
                logger.debug("_safe_get_contents hit: url={}", url)
                return res.results[0]

            # Primary returned nothing — try resolving via search
            doc_id = self._get_id_from_url(url)
            if not doc_id:
                logger.debug("_safe_get_contents miss: url={} (no doc_id)", url)
                return None

            res2 = self.client.get_contents([doc_id], text=True)
            if getattr(res2, "results", None):
                logger.debug("_safe_get_contents hit via doc_id: url={} doc_id={}", url, doc_id)
                return res2.results[0]
        except Exception as exc:
            logger.warning("_safe_get_contents failed for {}: {}", url, exc)
        return None

    def _enrich_paper_metadata(self, paper: Paper) -> Paper:
        """Fill in missing authors / published_date via an extra Exa call."""
        doc_id = cast(str, getattr(paper, "id", ""))
        logger.debug(
            "START _enrich_paper_metadata {} - initial authors={} url={}",
            doc_id,
            paper.authors,
            paper.url,
        )
        try:
            # 1. Check cache
            cached = self._cache.get(doc_id)
            if cached and cached["complete"] and self._cache.is_fresh(doc_id):
                return cast(Paper, _paper_utils.update_paper_from_meta(paper, cached["meta"]))

            # 2. Authoritative external metadata FIRST (arXiv / DOI) -------------
            external_meta: dict[str, Any] | None = None
            url_lower = str(paper.url).lower() if paper.url else ""

            is_arxiv_like = (
                "arxiv.org" in url_lower or "ar5iv.org" in url_lower
            ) and _url_utils.extract_arxiv_id(url_lower)

            arxiv_id_early: str | None = None
            doi_val_early: str | None = None

            if is_arxiv_like:
                arxiv_id_early = _url_utils.extract_arxiv_id(url_lower)
                logger.debug("Detected arXiv-like URL - id={}", arxiv_id_early)
                if arxiv_id_early:
                    logger.debug("Fetching arXiv metadata for {}", arxiv_id_early)
                    external_meta = _fetch_arxiv_metadata(arxiv_id_early)
                    # Fallback to Semantic Scholar if arXiv returned nothing (rate-limited)
                    if not external_meta:
                        logger.debug("arXiv empty, trying Semantic Scholar for {}", arxiv_id_early)
                        external_meta = _fetch_ss_arxiv(arxiv_id_early)
                    logger.debug(
                        "arXiv meta keys={} authors={}",
                        list(external_meta.keys()) if external_meta else None,
                        external_meta.get("authors") if external_meta else None,
                    )
            else:
                doi_val_early = _url_utils.extract_doi(url_lower)
                logger.debug("Detected DOI={}", doi_val_early)
                if doi_val_early:
                    logger.debug("Fetching Semantic Scholar DOI metadata for {}", doi_val_early)
                    external_meta = _fetch_doi_metadata(doi_val_early)
                    logger.debug(
                        "SS meta authors={}",
                        external_meta.get("authors") if external_meta else None,
                    )
                    if not external_meta:
                        logger.debug("Falling back to Crossref for {}", doi_val_early)
                        external_meta = _fetch_crossref_metadata(doi_val_early)
                        logger.debug(
                            "Crossref meta authors={}",
                            external_meta.get("authors") if external_meta else None,
                        )

            if external_meta:
                paper = cast(Paper, _paper_utils.update_paper_from_meta(paper, external_meta))
                if external_meta.get("text_content") and not paper.text_content:
                    paper.text_content = external_meta["text_content"]

            # Early exit: if we already have title + authors + date, skip Exa calls
            title_ok = bool(
                paper.title
                and paper.title != "No Title Provided"
                and not paper.title.startswith("arXiv:")
            )
            if title_ok and paper.authors and paper.published_date:
                complete_now = True
                self._cache.upsert(doc_id, paper.model_dump(), complete_now)
                return paper

            # 3. If still incomplete or stale, consult Exa get_contents ---------
            logger.debug("Exa get_contents for {}", doc_id)
            res = self.client.get_contents([doc_id], text=True)

            if not res.results or not getattr(res.results[0], "author", None):
                try:
                    queries: list[str] = [f'url:"{paper.url or doc_id}"']
                    if arxiv_id_early:
                        # Exa stores canonical metadata under the /abs/ page, not the /pdf/ blob.
                        abs_url = _url_utils.canonical_arxiv_abs_url(arxiv_id_early)
                        queries.append(f'url:"{abs_url}"')

                    for q in queries:
                        search_res = self.adapter.search(
                            q,
                            num_results=1,
                            contents={"text": True},
                            category="research paper",
                        )
                        if search_res.results:
                            res = search_res
                            break
                except Exception as _search_exc:
                    logger.debug("fallback_search_failed", url=paper.url, err=str(_search_exc))

            if res.results:
                r = res.results[0]
                # Unified author extraction from list *or* string
                authors_meta: list[str] | None = None
                authors_raw: Any = getattr(cast(Any, r), "authors", None)
                if authors_raw:
                    authors_meta = [str(a).strip() for a in authors_raw if str(a).strip()]
                elif getattr(r, "author", None):
                    authors_meta = [
                        n.strip() for n in str(getattr(r, "author", "")).split(",") if n.strip()
                    ]

                authors_meta = _author_utils.filter_plausible_authors(authors_meta)
                logger.debug("Exa results authors_raw={} | filtered={}", authors_raw, authors_meta)
                if not authors_meta:
                    authors_meta = None  # treat as missing for fallback logic

                # ----------------------------------------------------------
                # Only fill fields that are *still missing* after the higher
                # trust arXiv / DOI enrichment.  This prevents Exa’s scraped
                # metadata from overwriting clean values.
                # ----------------------------------------------------------
                meta: dict[str, Any] = {}
                if (
                    not paper.title
                    or paper.title == "No Title Provided"
                    or paper.title.startswith("arXiv:")
                ):
                    meta["title"] = getattr(r, "title", None)
                if (
                    (not paper.authors)
                    or not any(_author_utils.is_plausible_author(a) for a in paper.authors)
                ) and authors_meta:
                    meta["authors"] = authors_meta
                if not paper.published_date and getattr(r, "published_date", None):
                    meta["published_date"] = getattr(r, "published_date", None)

                # Skip update if Exa didn’t provide any *new* useful fields
                if meta:
                    paper = cast(Paper, _paper_utils.update_paper_from_meta(paper, meta))

                # Fallback to arXiv API if critical fields still missing
                _title_missing = (
                    not paper.title
                    or paper.title == "No Title Provided"
                    or paper.title.startswith("arXiv:")
                )
                if "arxiv.org" in str(paper.url).lower() and (
                    _title_missing
                    or not paper.authors
                    or not any(_author_utils.is_plausible_author(a) for a in paper.authors)
                    or not paper.published_date
                ):
                    arxiv_id = _url_utils.extract_arxiv_id(str(paper.url))
                    if arxiv_id:
                        arxiv_meta = _fetch_arxiv_metadata(arxiv_id)
                        if arxiv_meta:
                            paper = cast(
                                Paper, _paper_utils.update_paper_from_meta(paper, arxiv_meta)
                            )
                            if arxiv_meta.get("text_content") and not paper.text_content:
                                paper.text_content = arxiv_meta["text_content"]
                            logger.debug("Metadata enriched from arXiv API for {}", doc_id)

                        # Secondary: Semantic Scholar ARXIV endpoint if still missing
                        if (
                            not paper.authors
                            or not any(_author_utils.is_plausible_author(a) for a in paper.authors)
                        ) or not paper.published_date:
                            ss_meta = _fetch_ss_arxiv(arxiv_id)
                            if ss_meta:
                                paper = cast(
                                    Paper, _paper_utils.update_paper_from_meta(paper, ss_meta)
                                )
                                if ss_meta.get("text_content") and not paper.text_content:
                                    paper.text_content = ss_meta["text_content"]
                                logger.debug(
                                    "Metadata enriched from Semantic Scholar (arXiv) for {}", doc_id
                                )

                if (
                    (
                        not paper.authors
                        or not any(_author_utils.is_plausible_author(a) for a in paper.authors)
                    )
                    or not paper.published_date
                ) and "arxiv.org" not in str(paper.url).lower():
                    doi_val = _url_utils.extract_doi(str(paper.url))
                    if doi_val:
                        doi_meta = _fetch_doi_metadata(doi_val)
                        if doi_meta:
                            paper = cast(
                                Paper, _paper_utils.update_paper_from_meta(paper, doi_meta)
                            )
                            if doi_meta.get("text_content") and not paper.text_content:
                                paper.text_content = doi_meta["text_content"]
                            logger.debug(
                                "Metadata enriched from Semantic Scholar (DOI) for {}", doc_id
                            )

                complete_now = bool(paper.authors and paper.published_date)
                self._cache.upsert(doc_id, paper.model_dump(), complete_now)
        except Exception as exc:
            logger.debug("Metadata enrichment failed for {} - {}", doc_id, exc)
        logger.debug(
            "END _enrich_paper_metadata {} - final authors={} pub_date={}",
            doc_id,
            paper.authors,
            paper.published_date,
        )
        return paper

    def recheck_incomplete_metadata(self, limit: int = 100) -> int:
        """Re-run enrichment for docs still incomplete in cache. Returns processed count."""
        ids = self._cache.get_incomplete(limit=limit)
        processed = 0
        for doc_id in ids:
            try:
                res = self.client.get_contents([doc_id], text=True)
                if res.results:
                    r = res.results[0]
                    meta = {
                        "title": getattr(r, "title", None),
                        "authors": [
                            n.strip() for n in str(getattr(r, "author", "")).split(",") if n.strip()
                        ]
                        if getattr(r, "author", None)
                        else None,
                        "published_date": getattr(r, "published_date", None),
                    }
                    complete_now = bool(meta["authors"] and meta["published_date"])
                    self._cache.upsert(doc_id, meta, complete_now)
                    processed += 1
            except Exception as exc:
                logger.debug("recheck failed for {} - {}", doc_id, exc)
        return processed

    def answer(self, question: str, top_k: int = 3) -> dict[str, Any]:
        """Retrieve a short answer and supporting passages using Exa's /answer endpoint.

        Returns a dict with keys:
            - 'answer': str | None
            - 'passages': list[dict[str, str]]  # each: {'snippet': str, 'url': str}
        If Exa fails or no answer is found, returns an empty dict.
        """
        try:
            resp = self.adapter.answer(question, text=True)
            answer_text: str | None = getattr(resp, "answer", None)
            if isinstance(answer_text, dict):
                answer_text = str(answer_text)
            passages: list[dict[str, str]] = []
            # v2 answer() returns .citations instead of .results
            citations = getattr(resp, "citations", None) or getattr(resp, "results", None) or []
            for res in citations[:top_k]:
                snippet = getattr(res, "text", "").strip() or getattr(res, "snippet", "").strip()
                passages.append(
                    {
                        "snippet": snippet[:512],
                        "url": getattr(res, "url", "") or getattr(res, "id", ""),
                    }
                )
            return {"answer": answer_text, "passages": passages}
        except Exception as exc:
            logger.warning(
                "ExaClient.answer failed for question '{}' : {}", question, exc, exc_info=True
            )
            return {}

    def find_similar_papers(
        self,
        paper_url: str,
        num_results: int = 5,
        category: str = "research paper",
        *,
        start_published_date: datetime | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[Paper]:
        """
        Find papers similar to the one at the given URL using Exa's find_similar endpoint.

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
        request_count = 60  # Fetch up to 40 papers to ensure we get enough after filtering

        paper_url_norm = paper_url
        arxiv_id = _url_utils.extract_arxiv_id(paper_url)
        if arxiv_id:
            paper_url_norm = _url_utils.canonical_arxiv_abs_url(arxiv_id)

        papers: list[Paper] = []
        try:
            # Build parameter set for the Exa find_similar call
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

            # v2: contents=False for metadata-only similar results
            exa_kwargs.setdefault("contents", False)
            similar_response = self.client.find_similar(
                paper_url_norm,
                **exa_kwargs,
            )

            try:
                # Avoid dumping full metadata for every similar paper – that can flood logs.
                logger.debug(
                    "Exa similar results: {} items returned for URL {}",
                    len(similar_response.results),
                    paper_url,
                )
            except Exception:
                logger.warning("Failed to log Exa similar result count", url=paper_url)

            prelim: list[Paper] = []
            for result in similar_response.results:
                # Skip if the candidate URL is the same paper as the input URL
                cand_url: str | None = getattr(result, "url", None) or getattr(result, "id", None)
                if cand_url and _url_utils.url_equivalent(cand_url, paper_url_norm):
                    continue  # duplicate of main paper – skip

                p = _paper_utils.create_paper_from_exa_result(
                    result, f"Exa (similar to: {paper_url})"
                )
                if not p:
                    continue

                if not _url_utils.domain_allowed(p.url, include_domains, exclude_domains):
                    continue

                prelim.append(p)

            from concurrent.futures import as_completed

            if prelim:
                fut_map = {_ENRICH_POOL.submit(self._enrich_paper_metadata, p): p for p in prelim}
                for fut in as_completed(fut_map):
                    try:
                        paper = fut.result()
                    except Exception as enrich_exc:
                        failed_paper = fut_map.get(fut)
                        logger.debug(
                            "enrich failed",
                            id=getattr(failed_paper, "id", None),
                            err=str(enrich_exc),
                        )
                        continue

                    # Date filter now that we have enriched metadata
                    if (
                        start_published_date
                        and paper.published_date
                        and isinstance(paper.published_date, datetime)
                        and paper.published_date < start_published_date
                    ):
                        continue

                    url_str = str(paper.url)

                    # Heuristic author / scholarly check (unchanged)
                    if not paper.authors and (
                        not url_str.endswith(".pdf")
                        and not re.search(
                            r"arxiv\.org|doi\.org|acm\.org|ieee\.org|springer\.com|elsevier\.com|wiley\.com|nature\.com|sciencemag\.org|pubmed\.ncbi\.nlm\.nih\.gov|ssrn\.com|semanticscholar\.org|researchgate\.net",
                            url_str,
                        )
                    ):
                        continue

                    if _paper_utils.is_valid_paper(paper):
                        papers.append(paper)

            unique: dict[str, Paper] = {}
            for paper in papers:
                if paper and paper.url:
                    key = str(paper.url).lower()
                    if key in unique:
                        continue
                    unique[key] = paper

            papers = list(unique.values())

            # Keep the k highest-score similar papers
            if len(papers) > desired_count:
                papers_sorted = sorted(
                    papers,
                    key=lambda p: (p.score if p.score is not None else 0.0),
                    reverse=True,
                )
                papers = papers_sorted[:desired_count]

            logger.info(
                "find_similar_papers stats: pool={} → after_filters={} (requested={}) | per_source={}",
                len(similar_response.results),
                len(papers),
                desired_count,
                dict(Counter(p.source or "(unknown)" for p in papers)),
            )
            return papers
        except Exception as e:
            logger.error(
                "similar search failed",
                error=str(e),
                exc_info=True,
            )
            raise ExaAPIError(str(e)) from e

    def get_paper_details_and_similar_from_url(
        self,
        paper_url: str,
        num_similar_results: int = 5,
        *,
        category: str = "research paper",
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
        # Normalize arXiv PDF links to their canonical abstract page so Exa &
        # metadata enrichment return richer information.
        normalized_url = paper_url
        arxiv_id = _url_utils.extract_arxiv_id(paper_url)
        if arxiv_id and "arxiv.org/pdf" in paper_url.lower():
            normalized_url = _url_utils.canonical_arxiv_abs_url(arxiv_id)
            logger.info("Normalized arXiv PDF URL to abs page: {} → {}", paper_url, normalized_url)

        main_paper_object: Optional[Paper] = None
        similar_papers_list: list[Paper] = []

        # --------------------------------------------------------------
        # 0. Preferred: high-quality arXiv / DOI enrichment first
        # --------------------------------------------------------------
        try:
            main_paper_object = self.enrich_url(normalized_url)
            if main_paper_object:
                logger.info(
                    "enrich_url primary succeeded for {} - authors={} pub_date={}",
                    normalized_url,
                    len(main_paper_object.authors or []),
                    main_paper_object.published_date,
                )
        except Exception as enrich_exc:  # noqa: BLE001 – fall back gracefully
            logger.warning("primary enrich_url failed for {} - {}", normalized_url, enrich_exc)

        # 1. If still incomplete, try Exa get_contents for richer text/metadata
        needs_exa = (
            main_paper_object is None
            or not main_paper_object.authors
            or not main_paper_object.published_date
            or not main_paper_object.text_content
        )

        if needs_exa:
            try:
                logger.info("Fetching content for main URL (safe): {}", normalized_url)

                main_res = self._safe_get_contents(normalized_url)
                if main_res:
                    # Only construct a new Paper object from the Exa result when we
                    # *don’t already* have a high-quality object from the Metadata
                    # Enricher step.  Otherwise, merge in *only* missing fields so
                    # we never clobber the authoritative title / authors / date.
                    try:
                        if main_paper_object is None:
                            main_paper_object = _paper_utils.create_paper_from_exa_result(
                                main_res, f"Exa (safe_get_contents: {normalized_url})"
                            )
                        else:
                            meta_patch: dict[str, Any] = {
                                "text_content": getattr(main_res, "text", None),
                            }
                            # Only add title/authors/date if still missing.
                            for fld in ("title", "authors", "published_date"):
                                cur_val = getattr(main_paper_object, fld)
                                new_val = getattr(main_res, fld, None)
                                if (not cur_val) and new_val:
                                    meta_patch[fld] = new_val

                            if meta_patch:
                                main_paper_object = _paper_utils.update_paper_from_meta(
                                    main_paper_object, meta_patch
                                )
                    except Exception as conv_exc:  # noqa: BLE001 – log and fall back
                        logger.warning(
                            "create/merge paper from Exa failed for {} - {}",
                            normalized_url,
                            conv_exc,
                            exc_info=True,
                        )
            except Exception as e:
                logger.error(
                    "content fetch failed",
                    url=paper_url,
                    error=str(e),
                    exc_info=True,
                )

        # If Exa content fetch failed entirely, fall back to MetadataEnricher on the normalized URL.
        if main_paper_object is None:
            fallback_paper = self.enrich_url(normalized_url)
            logger.info("fallback enrich_url result for {} → {}", normalized_url, fallback_paper)
            if fallback_paper is not None:
                main_paper_object = fallback_paper

        # ------------------------------------------------------------------
        # Final URL canonicalisation: store upgraded PDF URL for consistency
        # ------------------------------------------------------------------
        if main_paper_object and main_paper_object.url:
            upgraded_url = _url_utils.upgrade_to_pdf_url(str(main_paper_object.url))
            if upgraded_url and upgraded_url != main_paper_object.url:
                main_paper_object.url = _paper_utils._coerce_http_url(upgraded_url)
                logger.debug("main_paper_url_upgraded", upgraded=upgraded_url)

        # --------------------------------------------------------------
        # Final authoritative pass: run MetadataEnricher again so any
        # high-quality arXiv / DOI metadata we might have missed is
        # merged in.  We *only* fill fields that remain empty to avoid
        # clobbering data.
        # --------------------------------------------------------------
        if main_paper_object and main_paper_object.url:
            try:
                enriched_main = self.enrich_url(str(main_paper_object.url))
                if enriched_main is not None:
                    main_paper_object = _paper_utils.update_paper_from_meta(
                        main_paper_object,
                        enriched_main.dict(exclude_none=True),
                    )
            except Exception as enr_exc:  # noqa: BLE001
                logger.warning(
                    "secondary enrich_url failed for {} - {}",
                    normalized_url,
                    enr_exc,
                )

        # 2. Find similar papers to the paper_url
        try:
            logger.info("Finding similar papers for URL: {}", normalized_url)

            similar_papers_list = self.find_similar_papers(
                normalized_url,
                num_results=num_similar_results,
                category=category,
                start_published_date=start_published_date,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
        except Exception as e:
            logger.error(
                "similar papers lookup failed",
                url=paper_url,
                error=str(e),
                exc_info=True,
            )
            # similar_papers_list remains empty or partially filled

        return {"main_paper": main_paper_object, "similar_papers": similar_papers_list}

    def _fetch_papers_from_exa(self, query: str, k: int, **kwargs: Any) -> list[Paper]:
        """Fetch a *fixed* pool of papers irrespective of display count, enrich metadata, then apply local filters."""
        request_count = self.MAX_RETURNED_PAPERS
        logger.info("fetch pool", query=query, requested=request_count)
        t_start = time.perf_counter()

        # 1. Fast metadata-only search (no full text)
        raw_pool = self.search_papers(query, num_results=request_count, **kwargs)

        # 1b. Cheap validity/domain/dedup before any network calls
        pre_pool = _filter_utils.pre_filter_papers(
            raw_pool,
            include_domains=kwargs.get("include_domains"),
            exclude_domains=kwargs.get("exclude_domains"),
        )

        # 2a. Batch-fetch arXiv metadata (single API call for all arXiv papers)
        arxiv_papers: dict[str, list[Paper]] = {}  # arxiv_id → papers with that id
        for p in pre_pool:
            aid = _url_utils.extract_arxiv_id(str(p.url) if p.url else "")
            if aid:
                arxiv_papers.setdefault(aid, []).append(p)

        if arxiv_papers:
            arxiv_ids = list(arxiv_papers.keys())
            batch_meta = _fetch_arxiv_batch(arxiv_ids)

            # Fallback: if arXiv returned nothing (e.g. 429), try Semantic Scholar
            if not batch_meta and arxiv_ids:
                logger.info(
                    "arXiv batch empty, falling back to Semantic Scholar for {} IDs", len(arxiv_ids)
                )
                batch_meta = _fetch_ss_arxiv_batch(arxiv_ids)

            for aid, meta in batch_meta.items():
                for p in arxiv_papers.get(aid, []):
                    _paper_utils.update_paper_from_meta(p, meta)

        # 2b. Enrich remaining metadata per-paper ----------------------------
        from concurrent.futures import as_completed

        def _needs_enrichment(p: Paper) -> bool:
            """Only enrich papers still missing critical metadata after batch fetch."""
            title_ok = bool(
                p.title and p.title != "No Title Provided" and not p.title.startswith("arXiv:")
            )
            authors_ok = bool(p.authors and len(p.authors) > 0)
            date_ok = bool(p.published_date)
            # Only enrich if title OR (authors AND date) are missing
            return not title_ok or not (authors_ok and date_ok)

        papers_to_enrich = [p for p in pre_pool if _needs_enrichment(p)]
        logger.info(
            "enrich_needed: {}/{} papers need per-paper enrichment",
            len(papers_to_enrich),
            len(pre_pool),
        )

        if papers_to_enrich:
            fut_map = {
                _ENRICH_POOL.submit(self._enrich_paper_metadata, p): p for p in papers_to_enrich
            }
            for fut in as_completed(fut_map):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001 – best-effort
                    failed_paper = fut_map.get(fut)
                    logger.debug(
                        "enrich_failed", id=getattr(failed_paper, "id", None), err=str(exc)
                    )

        # 3. Adaptive score / breadth filter -------------------------------
        filtered = _filter_utils.apply_score_threshold(pre_pool, k)

        # 4. Compute composite relevance scores ----------------------------
        _filter_utils.compute_relevance_scores(filtered)
        # Detailed pipeline stats -------------------------------------------
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)

        # User-friendly one-liner at INFO level
        logger.info(
            "search_pipeline: query='{}' raw={} pre={} final={} k_display={} ms={}",
            query,
            len(raw_pool),
            len(pre_pool),
            len(filtered),
            k,
            elapsed_ms,
        )

        # Detailed metrics at DEBUG level
        meta_missing_auth = sum(1 for p in pre_pool if not p.authors)
        meta_missing_date = sum(1 for p in pre_pool if not p.published_date)
        logger.debug(
            "pipeline_stats",
            query=query,
            raw=len(raw_pool),
            pre=len(pre_pool),
            no_authors=meta_missing_auth,
            no_date=meta_missing_date,
            final=len(filtered),
            k_display=k,
            elapsed_ms=elapsed_ms,
        )

        # Detailed list of papers that survived filtering – useful for debugging UI pool size issues.
        logger.info(
            "filtered_papers_list",
            papers=[
                {
                    "rank": idx + 1,
                    "id": p.id,
                    "title": (p.title or "")[:120],
                    "score": p.score,
                    "url": str(p.url) if p.url else None,
                }
                for idx, p in enumerate(filtered)
            ],
        )
        return filtered

    def _build_cache(self, papers: list[Paper], k: int, query: str) -> CachedSearch | None:
        """Construct a CachedSearch object from *papers* if possible.

        The helper converts the list into the dict/ordering structure required
        by CachedSearch and primes its display slots.  Returns ``None`` if the
        input list is empty or lacks IDs.
        """
        if not papers:
            return None

        paper_by_id = {p.id: p for p in papers if p.id}
        if not paper_by_id:
            return None

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

    def search_and_cache(
        self, query: str, k: int = 10, *, session_id: str = "_default", **kwargs: Any
    ) -> list[Paper]:
        """Fetches papers from Exa, caches them, and returns the initial display list."""
        papers = self._fetch_papers_from_exa(query, k, **kwargs)

        cached_search = self._build_cache(papers, k, query)
        if cached_search is None:
            self._cached_searches.pop(session_id, None)
            return []

        self._cached_searches[session_id] = cached_search
        return cast(list["Paper"], cached_search.get_display_papers())

    def url_search_and_cache(
        self,
        paper_url: str,
        *,
        k: int = 8,
        session_id: str = "_default",
        **kwargs: Any,
    ) -> list[Paper]:
        """Fetch *paper_url* and up to *k* similar papers, then cache.

        The input paper is treated as *pinned* and will always occupy
        position 0 in UI lists.  Shuffle / wildcard act on the remaining
        slots.
        """
        # Fetch details and similar papers (larger pool for caching)
        request_count = self.MAX_RETURNED_PAPERS
        url_results = self.get_paper_details_and_similar_from_url(
            paper_url,
            num_similar_results=request_count,
            category=kwargs.get("category", "research paper"),
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

            try:
                from services.paper.text_extractor import TextExtractor

                asyncio.create_task(
                    TextExtractor.instance().ensure_cached(main_paper.url, main_paper.id)
                )
            except Exception as bg_exc:
                logger.debug("TextExtractor background task failed to start: {}", bg_exc)
            pinned_id = main_paper.id if main_paper.id else None

        # Compute composite relevance scores for all papers in the URL pool
        _filter_utils.compute_relevance_scores(papers_pool)

        paper_by_id = {p.id: p for p in papers_pool if p.id}
        if not paper_by_id:
            self._cached_searches.pop(session_id, None)
            return []

        if pinned_id and pinned_id not in paper_by_id and main_paper and main_paper.id:
            paper_by_id[main_paper.id] = main_paper

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
        self._cached_searches[session_id] = cached
        self._url_pinned_ids[session_id] = pinned_id
        return cast(list["Paper"], cached.get_display_papers())

    def get_cached_search(self, session_id: str = "_default") -> CachedSearch | None:
        """Return the :class:`CachedSearch` for *session_id*, if any."""
        return self._cached_searches.get(session_id)

    def replace_wildcard(self, session_id: str = "_default") -> Optional[Paper]:
        """Swap the special *wildcard* slot for a fresh paper (when cached)."""
        cs = self._cached_searches.get(session_id)
        if cs is None:
            return None
        paper = cast(Optional["Paper"], cs.replace_wildcard())
        logger.info(
            "replace_wildcard: cached_pool={} returned_id={}",
            len(cs.paper_by_id),
            paper.id if paper else None,
        )
        return paper

    def shuffle_k(self, session_id: str = "_default") -> list[Paper]:
        """Randomly shuffle the current *k* display papers (excluding pinned)."""
        cs = self._cached_searches.get(session_id)
        if cs is None:
            return []
        shuffled = cs.shuffle_k()
        logger.info(
            "shuffle_k: cached_pool={} k_display={}",
            len(cs.paper_by_id),
            len(shuffled),
        )
        return cast(list[Paper], shuffled)

    def reset_display(self, session_id: str = "_default") -> list[Paper]:
        """Restore the cached paper list to its original deterministic ordering."""
        cs = self._cached_searches.get(session_id)
        return cast(list[Paper], cs.reset_display()) if cs is not None else []

    def clear_cache(self) -> None:
        """Drop all records from the on-disk PaperCache."""
        self._cache.clear()

    def reset_cached_search(self, session_id: str | None = None) -> None:  # noqa: D401
        """Clear the in-memory CachedSearch used for shuffle/reset UX.

        If *session_id* is None, clears **all** cached searches.
        """
        if session_id is None:
            self._cached_searches.clear()
        else:
            self._cached_searches.pop(session_id, None)
        logger.debug("cached_search_reset", session_id=session_id)

    def invalidate(self, doc_id: str) -> None:
        """Remove *doc_id* from cache so the next fetch is forced to hit the network."""
        self._cache.delete(doc_id)

    def enrich_url(self, url: str, timeout: float = 8.0) -> Optional[Paper]:
        """Synchronously return enriched metadata for *url* using MetadataEnricher.

        This is a thin wrapper around the async `MetadataEnricher.enrich`. It
        checks the local `PaperCache` first; otherwise it spins up an ad-hoc
        event loop (via `asyncio.run`) bounded by *timeout* seconds.
        """
        cache_id = url
        cached = self._cache.get(cache_id)
        if cached and cached["complete"] and self._cache.is_fresh(cache_id):
            logger.debug("enrich_url cache hit for {}", url)
            meta = cached["meta"]
            cached_paper = Paper(
                id=cache_id,
                title=meta.get("title", "No Title Provided"),
                url=_paper_utils._coerce_http_url(url),
            )
            cached_paper = cast(Paper, _paper_utils.update_paper_from_meta(cached_paper, meta))
            return cached_paper

        from services.paper.metadata.metadata_enricher import MetadataEnricher

        enricher = MetadataEnricher(self)

        async def _runner() -> Paper | None:
            try:
                return await asyncio.wait_for(enricher.enrich(url), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("enrich_url timed out for {} after {:.1f}s", url, timeout)
                return None

        # Determine if we're already inside an event loop.  Creating the coroutine only
        # after this check prevents the "coroutine was never awaited" warning.
        loop_running = True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop_running = False

        if not loop_running:
            # Safe to spin up a fresh event loop in this thread.
            paper = asyncio.run(_runner())
        else:
            # We are already inside an event loop; delegate execution to a worker thread.
            def _thread_runner() -> Paper | None:  # noqa: D401 – simple helper
                return asyncio.run(_runner())

            try:
                fut = _ENRICH_POOL.submit(_thread_runner)
                paper = fut.result(timeout=timeout + 1.0)
            except Exception as exc:
                logger.warning("enrich_url failed in worker thread for {} - {}", url, exc)
                return None

        if paper is None:
            return None

        complete_now = bool(paper.authors and paper.published_date)
        self._cache.upsert(paper.id, paper.model_dump(), complete_now)
        return paper

    def search_papers(self, query: str, num_results: int = 10, **kwargs: Any) -> list[Paper]:
        """Run **metadata-only** Exa search and convert to :class:`Paper` list."""
        # Exa SDK requires date parameters as ISO 8601 strings.
        if "start_published_date" in kwargs and isinstance(
            kwargs["start_published_date"], datetime
        ):
            kwargs["start_published_date"] = kwargs["start_published_date"].date().isoformat()
        if "end_published_date" in kwargs and isinstance(kwargs["end_published_date"], datetime):
            kwargs["end_published_date"] = kwargs["end_published_date"].date().isoformat()

        try:
            # v2: use auto search for best recall (blends neural + keyword);
            # contents=False for metadata-only to avoid fetching full text.
            kwargs.setdefault("type", "auto")
            kwargs.setdefault("contents", False)
            sdk_res = self.adapter.search(query, num_results=num_results, **kwargs)
        except Exception as exc:
            logger.error("Exa search failed for {} - {}", query, exc)
            return []

        papers: list[Paper] = []
        raw_pool_count: int = len(getattr(sdk_res, "results", []) or [])
        for res in getattr(sdk_res, "results", []) or []:
            p = _paper_utils.create_paper_from_exa_result(res, "Exa Search")
            if p is not None:
                papers.append(p)

        logger.info(
            "search_papers stats: pool={} → after_filters={} (requested={}) | per_source={}",
            raw_pool_count,
            len(papers),
            num_results,
            dict(Counter(p.source or "(unknown)" for p in papers)),
        )
        logger.debug(
            "search_papers fetched_list",
            papers=[
                {
                    "id": p.id,
                    "title": (p.title or "")[:120],
                    "url": str(p.url) if p.url else None,
                }
                for p in papers
            ],
        )
        return papers

    # ------------------------------------------------------------------
    # Public metadata helpers (thin wrappers) ---------------------------
    # ------------------------------------------------------------------

    def fetch_arxiv_metadata(self, arxiv_id: str, timeout: int = 10) -> dict[str, Any]:
        """Return metadata for *arxiv_id* via arXiv Atom feed."""

        return cast(dict[str, Any], _fetch_arxiv_metadata(arxiv_id, timeout=timeout))

    def fetch_doi_metadata(self, doi: str, timeout: int = 10) -> dict[str, Any]:
        """Return Semantic Scholar Graph metadata for *doi*."""

        return cast(dict[str, Any], _fetch_doi_metadata(doi, timeout=timeout))

    def fetch_crossref_metadata(self, doi: str, timeout: int = 10) -> dict[str, Any]:
        """Return Crossref metadata for *doi*."""

        return cast(dict[str, Any], _fetch_crossref_metadata(doi, timeout=timeout))
