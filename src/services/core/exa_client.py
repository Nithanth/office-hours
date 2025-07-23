"""High-level Exa search/orchestration service.

This module bridges Lux Scholar functionality with the official `exa_py` SDK.
It converts high-level application workflows (search, similarity, answer, and
enrichment) into concrete SDK calls, adds local caching & retry logic, and
maps Exa SDK results into first-class `Paper` domain objects.  Pure stateless
helpers live in `services.core.exa_utils`; any orchestration that requires
network access or shared caches remains here.
"""

import asyncio
import os
import re
import time
from collections import Counter
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
    fetch_crossref_metadata as _fetch_crossref_metadata,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_doi_metadata as _fetch_doi_metadata,
)
from services.paper.metadata.metadata_fetchers import (
    fetch_semantic_scholar_arxiv as _fetch_ss_arxiv,
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

        # New pooled search cache
        self._last_cached_search: Optional[CachedSearch] = None
        self._last_url_pinned_id: str | None = None

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
                category="research paper",
            )
            if resp.results:
                return resp.results[0].id
        except Exception as e:
            logger.warning("_get_id_from_url failed for {}: {}", url, e, exc_info=True)
        return None

    def _safe_get_contents(self, url: str) -> Any | None:
        """Best-effort wrapper around `get_contents` guaranteeing URL match."""
        try:
            res = self.client.get_contents([url], text=True)
            first_res = res.results[0] if getattr(res, "results", None) else None
            if first_res:
                snippet = (first_res.text[:100] + "…") if getattr(first_res, "text", None) else ""
                meta = {
                    "title": getattr(first_res, "title", None),
                    "authors": getattr(first_res, "authors", None),
                    "published_date": getattr(first_res, "published_date", None),
                    "url": getattr(first_res, "url", None)
                    or getattr(first_res, "canonical_url", None),
                    "id": getattr(first_res, "id", None),
                    "text_snippet": snippet,
                }
                logger.info("_safe_get_contents direct: url={} → meta={}", url, meta)
            else:
                logger.info("_safe_get_contents direct: url={} → no results", url)
            if res.results and _url_utils.url_equivalent(
                url, res.results[0].url or getattr(res.results[0], "canonical_url", None)
            ):
                return res.results[0]

            doc_id = self._get_id_from_url(url)
            if not doc_id:
                return None

            res2 = self.client.get_contents([doc_id], text=True)
            first_res2 = res2.results[0] if getattr(res2, "results", None) else None
            if first_res2:
                snippet2 = (
                    (first_res2.text[:100] + "…") if getattr(first_res2, "text", None) else ""
                )
                meta2 = {
                    "title": getattr(first_res2, "title", None),
                    "authors": getattr(first_res2, "authors", None),
                    "published_date": getattr(first_res2, "published_date", None),
                    "url": getattr(first_res2, "url", None)
                    or getattr(first_res2, "canonical_url", None),
                    "id": getattr(first_res2, "id", None),
                    "text_snippet": snippet2,
                }
                logger.info("_safe_get_contents by_id: doc_id={} → meta={}", doc_id, meta2)
            else:
                logger.info("_safe_get_contents by_id: doc_id={} → no results", doc_id)
            if res2.results:
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
                        search_res = self.adapter.search_and_contents(
                            q,
                            num_results=1,
                            text=True,
                            category="research paper",
                            use_autoprompt=False,
                        )
                        if search_res.results:
                            res = search_res
                            break
                except Exception as _:
                    pass

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
                if not paper.title or paper.title == "No Title Provided":
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
                if "arxiv.org" in str(paper.url).lower() and (
                    not paper.authors
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
                self._cache.upsert(doc_id, paper.dict(), complete_now)
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
        request_count = 60  # Fetch up to 40 papers to ensure we get enough after filtering

        paper_url_norm = paper_url
        arxiv_id = _url_utils.extract_arxiv_id(paper_url)
        if arxiv_id:
            paper_url_norm = _url_utils.canonical_arxiv_abs_url(arxiv_id)

        papers: list[Paper] = []
        try:
            # Build parameter set for the Exa *find_similar_and_contents* call
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

            similar_response = self.client.find_similar_and_contents(
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

            from concurrent.futures import ThreadPoolExecutor, as_completed

            # Run metadata enrichment in a bounded thread pool to avoid blocking I/O
            ENRICH_WORKERS = min(8, max(2, os.cpu_count() or 4))

            if prelim:
                with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as _pool:
                    fut_map = {_pool.submit(self._enrich_paper_metadata, p): p for p in prelim}
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
        try:
            needs_exa = (
                main_paper_object is None
                or not main_paper_object.authors
                or not main_paper_object.published_date
                or not main_paper_object.text_content
            )

            if not needs_exa:
                raise StopIteration  # skip Exa path; jump to similar search

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
        except StopIteration:
            # Skip Exa branch – already have quality metadata
            pass
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

        # 2. Enrich metadata for the pruned pool only ----------------------
        for p in pre_pool:
            has_trusted_id = bool(
                _url_utils.extract_arxiv_id(p.url)
                or _url_utils.extract_doi(str(p.url) if p.url else None)
            )

            if not has_trusted_id and p.authors and p.published_date:
                continue  # already complete enough for non-arXiv/DOI docs

            try:
                self._enrich_paper_metadata(p)
            except Exception as exc:  # noqa: BLE001 – best-effort
                failed_paper = p
                logger.debug("enrich_failed", id=getattr(failed_paper, "id", None), err=str(exc))

        # 3. Adaptive score / breadth filter -------------------------------
        filtered = _filter_utils.apply_score_threshold(pre_pool, k)
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

    def search_and_cache(self, query: str, k: int = 10, **kwargs: Any) -> list[Paper]:
        """Fetches papers from Exa, caches them, and returns the initial display list."""
        papers = self._fetch_papers_from_exa(query, k, **kwargs)

        cached_search = self._build_cache(papers, k, query)
        self._last_cached_search = cached_search

        if not cached_search:
            return []

        return cast(list["Paper"], cached_search.get_display_papers())

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

        paper_by_id = {p.id: p for p in papers_pool if p.id}
        if not paper_by_id:
            self._last_cached_search = None
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
        self._last_cached_search = cached
        self._last_url_pinned_id = pinned_id
        return cast(list["Paper"], cached.get_display_papers())

    def get_cached_search(self) -> CachedSearch | None:
        """Return the most recent :class:`CachedSearch`, if any."""
        return self._last_cached_search

    def replace_wildcard(self) -> Optional[Paper]:
        """Swap the special *wildcard* slot for a fresh paper (when cached)."""
        cs = self._last_cached_search
        if cs is None:
            return None
        paper = cast(Optional["Paper"], cs.replace_wildcard())
        logger.info(
            "replace_wildcard: cached_pool={} returned_id={}",
            len(cs.paper_by_id),
            paper.id if paper else None,
        )
        return paper

    def shuffle_k(self) -> list[Paper]:
        """Randomly shuffle the current *k* display papers (excluding pinned)."""
        cs = self._last_cached_search
        if cs is None:
            return []
        shuffled = cs.shuffle_k()
        logger.info(
            "shuffle_k: cached_pool={} k_display={}",
            len(cs.paper_by_id),
            len(shuffled),
        )
        return cast(list[Paper], shuffled)

    def reset_display(self) -> list[Paper]:
        """Restore the cached paper list to its original deterministic ordering."""
        cs = self._last_cached_search
        return cast(list[Paper], cs.reset_display()) if cs is not None else []

    def clear_cache(self) -> None:
        """Drop all records from the on-disk PaperCache."""
        self._cache.clear()

    def reset_cached_search(self) -> None:  # noqa: D401
        """Clear the in-memory CachedSearch used for shuffle/reset UX."""
        self._last_cached_search = None
        logger.debug("cached_search_reset")

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

        import concurrent.futures

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
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(_thread_runner)
                    paper = fut.result(timeout=timeout + 1.0)
            except Exception as exc:
                logger.warning("enrich_url failed in worker thread for {} - {}", url, exc)
                return None

        if paper is None:
            return None

        complete_now = bool(paper.authors and paper.published_date)
        self._cache.upsert(paper.id, paper.dict(), complete_now)
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
