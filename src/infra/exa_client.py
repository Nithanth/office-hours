import asyncio
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import suppress
from datetime import date, datetime, timezone
from typing import Any, Optional, cast
from urllib.parse import urlparse

from loguru import logger
from pydantic import HttpUrl

from .cached_search import CachedSearch
from .exa_adapter import ExaAdapter
from .paper_cache import PaperCache

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

        # SQLite paper metadata cache (no external service)
        self._cache = PaperCache()

        # New pooled search cache
        self._last_cached_search: Optional[CachedSearch] = None
        self._last_url_pinned_id: str | None = None

        # Semantic Scholar anonymous quota (≈100 req/day) tracker
        self._ss_req_day: date = datetime.utcnow().date()
        self._ss_req_count: int = 0

        # arXiv API throttle
        self._arxiv_next_ok: float = 0.0

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
                category="research paper",
            )
            if resp.results:
                return resp.results[0].id
        except Exception as e:
            logger.warning("_get_id_from_url failed for {}: {}", url, e, exc_info=True)
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

        # Normalise:
        #   • Lower-case for easier substring checks.
        #   • Strip query-string or fragment (e.g. "?download=1", "#foo").
        txt = str(url_or_text).strip().lower().split("?")[0].split("#")[0]

        # 1️⃣ New-style IDs — YYMM.NNNNN with optional version and ".pdf" suffix.
        #    Examples:
        #      • https://arxiv.org/pdf/2006.09661v1.pdf
        #      • 2006.09661v2
        m = re.search(r"(?P<id>\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", txt)
        if m:
            return m.group("id")

        # 2️⃣ Old category-based IDs — e.g. "cs/0307015" or "cs.LG/0307015v2".
        m = re.search(r"(?P<id>[a-z\-]+\/[0-9]{7})(?:v\d+)?", txt, re.IGNORECASE)
        if m:
            return m.group("id")

        # 3️⃣ Bare "arXiv:" prefix.
        m = re.search(r"arxiv:(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", txt)
        if m:
            return m.group("id")

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

    def _url_equivalent(self, a: str | None, b: str | None) -> bool:  # noqa: D401
        """Return *True* when *a* and *b* clearly refer to the same document.

        Normalises scheme, leading *www.*, trailing slashes and - for arXiv -
        compares the underlying identifier so that `/abs/` and `/pdf/` variants
        are treated as equal.
        """
        if not a or not b:
            return False

        def _norm(u: str) -> str:
            pu = urlparse(u)
            host = pu.netloc.lower().removeprefix("www.")
            path = pu.path.rstrip("/")

            # Special-case arXiv so /abs/ and /pdf/ map to the same key.
            if "arxiv.org" in host:
                m = re.search(r"(?:abs|pdf)/(\d{4}\.\d{4,5})", path)
                if m:
                    return f"arxiv:{m.group(1)}"

            return f"{host}{path}"

        return _norm(a) == _norm(b)

    def _safe_get_contents(self, url: str) -> Any | None:  # noqa: ANN401 - Exa SDK types
        """Best-effort wrapper around `get_contents` guaranteeing URL match."""
        try:
            res = self.client.get_contents([url], text=True)
            if res.results and self._url_equivalent(
                url, res.results[0].url or getattr(res.results[0], "canonical_url", None)
            ):
                return res.results[0]

            doc_id = self._get_id_from_url(url)
            if not doc_id:
                return None

            res2 = self.client.get_contents([doc_id], text=True)
            if res2.results:
                return res2.results[0]
        except Exception as exc:  # noqa: BLE001
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
                return self._update_paper_from_meta(paper, cached["meta"])

            # 2. Authoritative external metadata FIRST (arXiv / DOI) -------------
            external_meta: dict[str, Any] | None = None
            url_lower = str(paper.url).lower() if paper.url else ""

            # Detect arXiv-hosted PDFs (arxiv.org/pdf/…) and ar5iv mirrors.
            is_arxiv_like = (
                "arxiv.org" in url_lower or "ar5iv.org" in url_lower
            ) and self._extract_arxiv_id(url_lower)

            arxiv_id_early: str | None = None
            doi_val_early: str | None = None

            if is_arxiv_like:
                arxiv_id_early = self._extract_arxiv_id(url_lower)
                logger.debug("Detected arXiv-like URL - id={}", arxiv_id_early)
                if arxiv_id_early:
                    logger.debug("Fetching arXiv metadata for {}", arxiv_id_early)
                    external_meta = self._fetch_arxiv_metadata(arxiv_id_early)
                    logger.debug(
                        "arXiv meta keys={} authors={}",
                        list(external_meta.keys()) if external_meta else None,
                        external_meta.get("authors") if external_meta else None,
                    )
            else:
                doi_val_early = self._extract_doi(url_lower)
                logger.debug("Detected DOI={}", doi_val_early)
                if doi_val_early:
                    logger.debug("Fetching Semantic Scholar DOI metadata for {}", doi_val_early)
                    external_meta = self._fetch_doi_metadata(doi_val_early)
                    logger.debug(
                        "SS meta authors={}",
                        external_meta.get("authors") if external_meta else None,
                    )
                    if not external_meta:
                        logger.debug("Falling back to Crossref for {}", doi_val_early)
                        external_meta = self._fetch_crossref_metadata(doi_val_early)
                        logger.debug(
                            "Crossref meta authors={}",
                            external_meta.get("authors") if external_meta else None,
                        )

            if external_meta:
                paper = self._update_paper_from_meta(paper, external_meta)
                if external_meta.get("text_content") and not paper.text_content:
                    paper.text_content = external_meta["text_content"]

            # 3. If still incomplete or stale, consult Exa get_contents ---------
            logger.debug("Exa get_contents for {}", doc_id)
            res = self.client.get_contents([doc_id], text=True)

            # If still missing critical fields, try one or more targeted searches
            # which sometimes have richer metadata than get_contents.
            if not res.results or not getattr(res.results[0], "author", None):
                try:
                    # Build a small set of fallback queries ordered by relevance.
                    queries: list[str] = [f'url:"{paper.url or doc_id}"']
                    if arxiv_id_early:
                        # Exa stores canonical metadata under the /abs/ page, not the /pdf/ blob.
                        abs_url = f"https://arxiv.org/abs/{arxiv_id_early}"
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

                authors_meta = self._filter_plausible_authors(authors_meta)
                logger.debug("Exa results authors_raw={} | filtered={}", authors_raw, authors_meta)
                if not authors_meta:
                    authors_meta = None  # treat as missing for fallback logic

                meta = {
                    "title": getattr(r, "title", None),
                    "authors": authors_meta,
                    "published_date": getattr(r, "published_date", None),
                }
                paper = self._update_paper_from_meta(paper, meta)

                # Fallback to arXiv API if critical fields still missing
                if "arxiv.org" in str(paper.url).lower() and (
                    not paper.authors
                    or not any(self._is_plausible_author(a) for a in paper.authors)
                    or not paper.published_date
                ):
                    arxiv_id = self._extract_arxiv_id(str(paper.url))
                    if arxiv_id:
                        arxiv_meta = self._fetch_arxiv_metadata(arxiv_id)
                        if arxiv_meta:
                            paper = self._update_paper_from_meta(paper, arxiv_meta)
                            if arxiv_meta.get("text_content") and not paper.text_content:
                                paper.text_content = arxiv_meta["text_content"]
                            logger.debug("Metadata enriched from arXiv API for {}", doc_id)

                        # Secondary: Semantic Scholar ARXIV endpoint if still missing
                        if (
                            not paper.authors
                            or not any(self._is_plausible_author(a) for a in paper.authors)
                        ) or not paper.published_date:
                            ss_meta = self._fetch_semantic_scholar_arxiv(arxiv_id)
                            if ss_meta:
                                paper = self._update_paper_from_meta(paper, ss_meta)
                                if ss_meta.get("text_content") and not paper.text_content:
                                    paper.text_content = ss_meta["text_content"]
                                logger.debug(
                                    "Metadata enriched from Semantic Scholar (arXiv) for {}", doc_id
                                )

                # DOI fallback via Semantic Scholar if still incomplete and not arXiv
                if (
                    (
                        not paper.authors
                        or not any(self._is_plausible_author(a) for a in paper.authors)
                    )
                    or not paper.published_date
                ) and "arxiv.org" not in str(paper.url).lower():
                    doi_val = self._extract_doi(str(paper.url))
                    if doi_val:
                        doi_meta = self._fetch_doi_metadata(doi_val)
                        if doi_meta:
                            paper = self._update_paper_from_meta(paper, doi_meta)
                            if doi_meta.get("text_content") and not paper.text_content:
                                paper.text_content = doi_meta["text_content"]
                            logger.debug(
                                "Metadata enriched from Semantic Scholar (DOI) for {}", doc_id
                            )

                complete_now = bool(paper.authors and paper.published_date)
                self._cache.upsert(doc_id, paper.dict(), complete_now)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Metadata enrichment failed for {} - {}", doc_id, exc)
        logger.debug(
            "END _enrich_paper_metadata {} - final authors={} pub_date={}",
            doc_id,
            paper.authors,
            paper.published_date,
        )
        return paper

    # --- Author heuristics --------------------------------------------
    _NAME_PATTERN = re.compile(r"[A-Z][a-z].*")  # crude: at least one capital followed by lowercase

    @classmethod
    def _is_plausible_author(cls, token: str) -> bool:
        """Return *True* if *token* looks like a real personal name."""
        t = token.strip()
        if len(t) < 2:
            return False
        # Strip common trailing punctuation that sneaks in (commas, semicolons, asterisks)
        t = t.rstrip(",;*")
        # Skip pure lowercase/username-style strings (e.g., 'konasj')
        if t == t.lower() and " " not in t:
            return False
        # Reject if it still contains characters other than letters, dots, spaces or hyphens
        if re.search(r"[^A-Za-z.\-\s]", t):
            return False
        # Very crude check for at least one capitalised segment
        if not cls._NAME_PATTERN.search(t):
            return False
        # Exclude tokens that begin with '[' or contain 'submitted'
        bad_starts = ("[",)
        if t.startswith(bad_starts):
            return False

        # Exclude tokens mentioning submission or revision dates
        if any(kw in t.lower() for kw in ("submitted", "revised")):
            return False

        # If no whitespace present, treat as initials only when very short (≤3)
        # to exclude single-word handles like "Konasj".
        if " " not in t and "." not in t:
            short_suffixes = {"jr", "sr", "iii", "iv"}
            if len(t) > 3 and t.lower() not in short_suffixes:
                return False

        return True

    @classmethod
    def _filter_plausible_authors(cls, authors: list[str] | None) -> list[str]:
        """Return subset of *authors* passing `_is_plausible_author`."""
        if not authors:
            return []
        return [a for a in authors if cls._is_plausible_author(a)]

    # ---------------------------------------------------------------------

    def _update_paper_from_meta(self, paper: Paper, meta: dict[str, Any]) -> Paper:
        """Helper: overlay non-empty meta fields onto paper object."""
        if meta.get("title") and (not paper.title or paper.title == "No Title Provided"):
            paper.title = meta["title"]
        # Replace authors if missing *or* existing list looks invalid
        if meta.get("authors"):
            # Always re-sanitise in case caller passed unfiltered data
            clean_authors = self._filter_plausible_authors(cast(list[str], meta["authors"]))
            logger.debug(
                "_update_paper_from_meta: incoming authors={} | clean_authors={} | existing={}",
                meta.get("authors"),
                clean_authors,
                paper.authors,
            )
            existing_ok = False
            if paper.authors:
                # Treat existing list as sufficient only if *at least one* author token
                # appears to be a full name – a crude proxy is the presence of a space
                # character (e.g., "Yee Whye Teh") in addition to basic plausibility.
                # This avoids cases where Exa returns a list of single-word tokens
                # ("Dupont", "Emilien", …) which would otherwise be marked *plausible*
                # and block a higher-quality list from arXiv / Semantic Scholar.
                any_full_name = any(" " in t for t in paper.authors)
                if (
                    any_full_name
                    and any(self._is_plausible_author(tok) for tok in paper.authors)
                    and len(paper.authors) >= max(3, len(clean_authors) - 1)
                ):
                    existing_ok = True
            logger.debug("existing_ok={}", existing_ok)
            if not existing_ok:
                paper.authors = clean_authors
        if meta.get("published_date") and not paper.published_date:
            with suppress(Exception):
                paper.published_date = datetime.fromisoformat(str(meta["published_date"])).replace(
                    tzinfo=timezone.utc
                )
        # Optionally overlay text_content if we do not yet have any.
        if meta.get("text_content") and not paper.text_content:
            paper.text_content = meta["text_content"]
        return paper

    # ---------------------------------------------------------------------

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
            except Exception as exc:  # noqa: BLE001
                logger.debug("recheck failed for {} - {}", doc_id, exc)
        return processed

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
                "ExaClient.answer failed for question '{}' : {}", question, exc, exc_info=True
            )
            return {}

    # ---------------------------------------------------------------------

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

        # The find_similar API does not support server-side date filters. If
        # callers need date constraints, they should post-filter the returned
        # list.  For now we skip any local parsing to avoid unused-variable
        # lint errors.

        # Normalize URL (especially arXiv PDFs → canonical /abs/ form) to maximise
        # Exa’s chance of recognising the document and its neighbourhood.
        paper_url_norm = paper_url
        arxiv_id = self._extract_arxiv_id(paper_url)
        if arxiv_id:
            paper_url_norm = self._canonical_arxiv_abs_url(arxiv_id)

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

            # Use find_similar_and_contents but skip full text download for every
            # candidate. We fetch only metadata here (text=False) and let the
            # later _enrich_paper_metadata call retrieve full text *only* for
            # the handful of papers that survive our filters. This cuts
            # latency ~70 % while preserving functionality.
            similar_response = self.client.find_similar_and_contents(
                paper_url_norm,
                **exa_kwargs,
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

            # --------------------------------------------------
            # 1) Light pre-filter (domain, date) BEFORE enrichment so we don't
            #    waste network calls on obviously unwanted items.
            # --------------------------------------------------

            prelim: list[Paper] = []
            for result in similar_response.results:
                p = self._create_paper_from_exa_result(result, f"Exa (similar to: {paper_url})")
                if not p:
                    continue

                # Domain filter first (cheap, metadata-only)
                if not self._domain_allowed(p.url, include_domains, exclude_domains):
                    continue

                # Optional date cut *after* we enrich (needs published_date), so we skip here.

                prelim.append(p)

            # --------------------------------------------------
            # 2) Concurrent metadata enrichment (I/O bound).
            # --------------------------------------------------
            from concurrent.futures import ThreadPoolExecutor, as_completed

            ENRICH_WORKERS = min(8, max(2, os.cpu_count() or 4))

            if prelim:
                with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as _pool:
                    fut_map = {_pool.submit(self._enrich_paper_metadata, p): p for p in prelim}
                    for fut in as_completed(fut_map):
                        try:
                            paper = fut.result()
                        except Exception as enrich_exc:  # noqa: BLE001
                            logger.debug("enrich failed: {}", enrich_exc)
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

                        if self._is_valid_paper(paper):
                            papers.append(paper)

            # Deduplicate results on URL equivalence client-side since Exa
            # no longer supports the `exclude_duplicates` parameter.
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

            # Summary logging -------------------------------------------------
            logger.info(
                "find_similar_papers stats: pool={} → after_filters={} (requested={}) | per_source={}",
                len(similar_response.results),
                len(papers),
                desired_count,
                dict(Counter(p.source or "(unknown)" for p in papers)),
            )
            return papers
        except Exception as e:
            logger.error(f"Error during Exa API similar papers search: {e}", exc_info=True)
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
        main_paper_object: Optional[Paper] = None
        similar_papers_list: list[Paper] = []

        # 1. Fetch content for the main paper_url
        try:
            logger.info("Fetching content for main URL (safe): {}", paper_url)

            main_res = self._safe_get_contents(paper_url)
            if main_res:
                main_paper_object = self._create_paper_from_exa_result(
                    main_res, f"Exa (safe_get_contents: {paper_url})"
                )
                if main_paper_object and main_paper_object.url:
                    enriched_main = self.enrich_url(str(main_paper_object.url))
                    if enriched_main is not None:
                        main_paper_object = enriched_main

        except Exception as e:
            logger.error(
                f"Error fetching content for main URL {paper_url} with Exa: {e}", exc_info=True
            )
            # main_paper_object remains None

        # 2. Find similar papers to the paper_url
        try:
            logger.info("Finding similar papers for URL: {}", paper_url)

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

    def _fetch_papers_from_exa(self, query: str, k: int, **kwargs: Any) -> list[Paper]:
        """Fetches a pool of papers from the Exa API."""
        request_count = min(k * 10, 50)
        logger.info(f"Fetching {request_count} papers from Exa for query: '{query}'")
        return self.search_papers(query, num_results=request_count, **kwargs)

    def _build_cache(self, papers: list[Paper], k: int, query: str) -> CachedSearch | None:
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

    def search_and_cache(self, query: str, k: int = 10, **kwargs: Any) -> list[Paper]:
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

            # --------------------------------------------------
            # Kick off background full-text extraction
            # --------------------------------------------------
            try:
                from infra.text_extractor import TextExtractor

                asyncio.create_task(
                    TextExtractor.instance().ensure_cached(main_paper.url, main_paper.id)
                )
            except Exception as bg_exc:  # noqa: BLE001 - background should never break flow
                logger.debug("TextExtractor background task failed to start: {}", bg_exc)
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

    def replace_wildcard(self) -> Optional[Paper]:
        cs = self._last_cached_search
        if cs is None:
            return None
        return cast(Optional["Paper"], cs.replace_wildcard())

    def shuffle_k(self) -> list[Paper]:
        cs = self._last_cached_search
        return cs.shuffle_k() if cs is not None else []

    def reset_display(self) -> list[Paper]:
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

        * include_domains - at least one must match if provided.
        * exclude_domains - none may match.

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
        except Exception:  # pragma: no cover - broken URLs
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

        # bioRxiv / medRxiv - convert /content/<id>[.something] to .full.pdf
        if "biorxiv.org" in url_str or "medrxiv.org" in url_str:
            parsed = urlparse(url_str)
            if parsed.path.startswith("/content/") and not parsed.path.endswith(".pdf"):
                base_path = parsed.path.rstrip("/")
                return f"https://{parsed.netloc}{base_path}.full.pdf"

        # IEEE Xplore - map /document/<id> to stamp PDF link
        if "ieeexplore.ieee.org" in url_str:
            parsed = urlparse(url_str)
            m = re.search(r"/document/(\d+)", parsed.path)
            if m:
                doc_id = m.group(1)
                return f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={doc_id}"

        # ACM Digital Library - append /pdf to DOI pages
        if "dl.acm.org" in url_str:
            parsed = urlparse(url_str)
            if parsed.path.startswith("/doi/") and not parsed.path.endswith("/pdf"):
                return f"https://{parsed.netloc}{parsed.path}/pdf"

        # OpenReview - convert forum or landing page URLs to the canonical PDF endpoint
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

        # ACL Anthology (aclanthology.org / aclweb.org) - append .pdf if missing
        if "aclanthology.org" in url_str or "aclweb.org" in url_str:
            parsed = urlparse(url_str)
            # Already direct PDF link
            if parsed.path.lower().endswith(".pdf"):
                return url_str
            clean_path = parsed.path.rstrip("/")
            if clean_path:
                return f"https://{parsed.netloc}{clean_path}.pdf"

        # TODO: Add more domain-specific handlers here as needed.
        return url_str

    # ------------------------------------------------------------------
    # Utility helpers (needed by demo block and elsewhere)
    # ------------------------------------------------------------------

    @staticmethod
    def is_url(text: str) -> bool:  # noqa: D401  (simple function)
        """Return True if *text* looks like an academic paper URL."""
        url_pattern = re.compile(
            r"^(https?://)"
            r"("  # start group of domains
            r"([\w.-]+\.)*(arxiv\.org|biorxiv\.org|medrxiv\.org|doi\.org|acm\.org|ieee\.org|springer\.com|elsevier\.com|wiley\.com|nature\.com|sciencemag\.org|pubmed\.ncbi\.nlm\.nih\.gov|ssrn\.com|semanticscholar\.org|researchgate\.net)"
            r"(/\S*)?"  # optional path
            r"|"  # or generic domain with at least one path segment
            r"([\w-]+\.)+[A-Za-z]{2,6}(/([\w.-]+)){1,}/?"  # generic domain
            r")$",
            re.IGNORECASE,
        )
        return bool(url_pattern.match(text.strip()))

    def _create_paper_from_exa_result(
        self, result: Any, source_description: str
    ) -> Optional[Paper]:
        """Convert a raw Exa SDK result into our `Paper` model.

        This lighter-weight re-implementation recreates the previous logic but
        in a concise form, and always funnels author tokens through
        `_is_plausible_author` to avoid junk such as "konasj".
        """
        try:
            # Title and URL ---------------------------------------------------
            title_val: str = getattr(result, "title", None) or "No Title Provided"
            raw_url: str = getattr(result, "url", "")
            # _upgrade_to_pdf_url may return *None*; fall back to original.
            url_val: str = self._upgrade_to_pdf_url(raw_url) or raw_url

            # Authors --------------------------------------------------------
            raw_authors_any = getattr(result, "authors", None) or getattr(result, "author", None)
            authors_list: list[str] = []
            if authors_attr := getattr(result, "authors", None):
                authors_list = [
                    str(a).strip() for a in authors_attr if self._is_plausible_author(str(a))
                ]
            elif author_str := getattr(result, "author", None):
                # Fallback single string - split common delimiters
                tokens = re.split(r",|;|\band\b", str(author_str))
                authors_list = [t.strip() for t in tokens if self._is_plausible_author(t)]

            logger.debug(
                "_create_paper_from_exa_result raw_authors={} | kept={}",
                raw_authors_any,
                authors_list,
            )

            # Published date --------------------------------------------------
            published_date: datetime | None = None
            if pd_str := getattr(result, "published_date", None):
                with suppress(Exception):
                    if "T" in pd_str:
                        published_date = datetime.fromisoformat(pd_str.replace("Z", "+00:00"))
                    else:
                        published_date = datetime.strptime(pd_str, "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )

            # Text content (abstract/full) -----------------------------------
            text_content: str | None = None
            if getattr(result, "text", None):
                text_content = result.text
            elif getattr(result, "summary", None):
                text_content = result.summary

            paper_dict: dict[str, Any] = {
                "id": getattr(result, "id", None) or url_val,
                "title": title_val,
                "url": cast(HttpUrl, url_val),
                "authors": authors_list,
                "text_content": text_content,
                "published_date": published_date,
                "score": getattr(result, "score", None),
                "source": source_description,
            }

            return cast("Paper", Paper(**paper_dict))

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to build Paper from Exa result ({}) - {}",
                source_description,
                exc,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Simple wrapper around adapter.search_and_contents
    # ------------------------------------------------------------------

    def search_papers(self, query: str, num_results: int = 10, **kwargs: Any) -> list[Paper]:
        """High-level helper: run Exa *search_and_contents* and convert to Paper list."""
        try:
            sdk_res = self.adapter.search_and_contents(query, num_results=num_results, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.error("Exa search_and_contents failed for {} - {}", query, exc)
            return []

        papers: list[Paper] = []
        raw_pool_count: int = len(getattr(sdk_res, "results", []) or [])
        for res in getattr(sdk_res, "results", []) or []:
            p = self._create_paper_from_exa_result(res, "Exa Search")
            if p is not None:
                papers.append(p)

        logger.info(
            "search_papers stats: pool={} → after_filters={} (requested={}) | per_source={}",
            raw_pool_count,
            len(papers),
            num_results,
            dict(Counter(p.source or "(unknown)" for p in papers)),
        )
        return papers

    # ---------------------------------------------------------------------
    # Metadata merge helpers (trust-tier aware)
    # ---------------------------------------------------------------------

    @staticmethod
    def _merge_dicts_keep_first(
        primary: dict[str, Any], secondary: dict[str, Any]
    ) -> dict[str, Any]:
        """Return *secondary* enriched by *primary* without overriding existing keys.

        Useful for composing metadata from multiple sources with an explicit
        trust order: call this with the **higher-trust** dict first so that its
        non-empty values win.

        Example:
            high = {"title": "Ground-Truth", "authors": ["A"]}
            low  = {"title": "Noisy", "published_date": "2020-01-01"}
            merged = ExaClient._merge_dicts_keep_first(high, low)
            # -> {"title": "Ground-Truth", "authors": ["A"], "published_date": "2020-01-01"}
        """
        out: dict[str, Any] = dict(secondary)  # make a shallow copy
        for k, v in primary.items():
            if v:  # only consider truthy values
                out.setdefault(k, v)
        return out

    # ------------------------------------------------------------------
    # Public cache helpers
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Drop all records from the on-disk PaperCache."""
        self._cache.clear()

    def invalidate(self, doc_id: str) -> None:
        """Remove *doc_id* from cache so the next fetch is forced to hit the network."""
        self._cache.delete(doc_id)

    # ------------------------------------------------------------------
    # Public convenience: blocking enrichment for arbitrary URL
    # ------------------------------------------------------------------

    def enrich_url(self, url: str, timeout: float = 8.0) -> Optional[Paper]:
        """Synchronously return enriched metadata for *url* using MetadataEnricher.

        This is a thin wrapper around the async `MetadataEnricher.enrich`. It
        checks the local `PaperCache` first; otherwise it spins up an ad-hoc
        event loop (via `asyncio.run`) bounded by *timeout* seconds.
        """
        # Fast path: URL-keyed cache hit and still fresh
        cache_id = url
        cached = self._cache.get(cache_id)
        if cached and cached["complete"] and self._cache.is_fresh(cache_id):
            logger.debug("enrich_url cache hit for {}", url)
            meta = cached["meta"]
            cached_paper = Paper(
                id=cache_id,
                title=meta.get("title", "No Title Provided"),
                url=cast(HttpUrl, url),
            )
            cached_paper = self._update_paper_from_meta(cached_paper, meta)
            return cached_paper

        # Lazy import to avoid circular dep (MetadataEnricher imports ExaClient)
        from .metadata_enricher import MetadataEnricher  # noqa: E402 - local import by design

        enricher = MetadataEnricher(self)

        async def _runner() -> Paper | None:  # noqa: D401 - simple async wrapper
            try:
                return await asyncio.wait_for(enricher.enrich(url), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("enrich_url timed out for {} after {:.1f}s", url, timeout)
                return None

        try:
            paper = asyncio.run(_runner())
        except RuntimeError:
            # Called from within an existing event loop (e.g. FastAPI handler).
            # Spin up a *separate* thread + event loop to execute the coroutine
            # synchronously so we don't block the current loop and avoid
            # `coroutine was never awaited` warnings.
            import concurrent.futures

            def _thread_runner() -> Paper | None:
                return asyncio.run(_runner())

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(_thread_runner)
                    paper = fut.result(timeout=timeout + 1.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("enrich_url failed in nested loop for {} - {}", url, exc)
                return None

        if paper is None:
            return None

        complete_now = bool(paper.authors and paper.published_date)
        self._cache.upsert(paper.id, paper.dict(), complete_now)
        return paper

    # ------------------------------------------------------------------
    # Lightweight external metadata fetchers (restored stubs)
    # ------------------------------------------------------------------

    def _fetch_arxiv_metadata(self, arxiv_id: str, timeout: int = 10) -> dict[str, Any]:
        """Fetch basic metadata from arXiv public API.

        Implements only title/author/date fields using std-lib. If any network
        error occurs we return an empty dict to let fallbacks run.
        """

        # Very light courtesy throttle (1 req / 3 s)
        now = time.time()
        if now < self._arxiv_next_ok:
            time.sleep(self._arxiv_next_ok - now)

        api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
        req = urllib.request.Request(
            api_url,
            headers={"User-Agent": "LuxScholarBot/0.1 (metadata enrichment)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_bytes: bytes = resp.read()

            self._arxiv_next_ok = time.time() + 3.2

            root = ET.fromstring(xml_bytes)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entry = root.find("atom:entry", ns)
            if entry is None:
                return {}

            title_el = entry.find("atom:title", ns)
            title: str | None = None
            if title_el is not None and title_el.text:
                title = title_el.text.strip().replace("\n", " ")

            authors: list[str] = []
            for auth_el in entry.findall("atom:author", ns):
                nm_el = auth_el.find("atom:name", ns)
                if nm_el is not None and nm_el.text:
                    nm = nm_el.text.strip()
                    if nm and self._is_plausible_author(nm):
                        authors.append(nm)

            published_el = entry.find("atom:published", ns)
            published: str | None = None
            if published_el is not None and published_el.text:
                published = published_el.text.strip()

            meta: dict[str, Any] = {}
            if title:
                meta["title"] = title
            if authors:
                meta["authors"] = authors
            if published:
                meta["published_date"] = published
            return meta
        except Exception as exc:  # noqa: BLE001
            logger.debug("arXiv fetch failed for {} - {}", arxiv_id, exc)
            return {}

    _DOI_REGEX = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)

    @classmethod
    def _extract_doi(cls, text: str | None) -> Optional[str]:
        if not text:
            return None
        m = cls._DOI_REGEX.search(text)
        return m.group(0).lower() if m else None

    def _fetch_doi_metadata(self, doi: str, timeout: int = 10) -> dict[str, Any]:
        """Minimal Semantic Scholar Graph API fetch (anonymous)."""

        today = datetime.utcnow().date()
        if today != self._ss_req_day:
            self._ss_req_day = today
            self._ss_req_count = 0
        if self._ss_req_count >= 100:
            return {}

        api_url = (
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
            "?fields=title,authors,year"
        )
        try:
            with urllib.request.urlopen(api_url, timeout=timeout) as resp:
                if resp.status == 429:
                    self._ss_req_count = 100
                    return {}
                data = json.load(resp)

            self._ss_req_count += 1
            meta: dict[str, Any] = {}
            if data.get("title"):
                meta["title"] = data["title"].strip()
            if data.get("authors"):
                names = [a.get("name", "").strip() for a in data["authors"] if a.get("name")]
                names = [n for n in names if self._is_plausible_author(n)]
                if names:
                    meta["authors"] = names
            if data.get("year"):
                meta["published_date"] = f"{data['year']}-01-01"
            return meta
        except Exception as exc:  # noqa: BLE001
            logger.debug("SemanticScholar DOI fetch failed for {} - {}", doi, exc)
            return {}

    def _fetch_crossref_metadata(self, doi: str, timeout: int = 10) -> dict[str, Any]:
        """Lightweight Crossref JSON fetch."""
        import re as _re

        api_url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        try:
            with urllib.request.urlopen(api_url, timeout=timeout) as resp:
                data = json.load(resp).get("message", {})
            meta: dict[str, Any] = {}
            if data.get("title"):
                meta["title"] = data["title"][0]
            if data.get("author"):
                names = []
                for a in data["author"]:
                    full = f"{a.get('given', '').strip()} {a.get('family', '').strip()}".strip()
                    if full and self._is_plausible_author(full):
                        names.append(full)
                if names:
                    meta["authors"] = names
            if data.get("issued", {}).get("date-parts"):
                parts = data["issued"]["date-parts"][0]
                year = parts[0]
                month = parts[1] if len(parts) > 1 else 1
                day = parts[2] if len(parts) > 2 else 1
                meta["published_date"] = f"{year:04d}-{month:02d}-{day:02d}"
            if data.get("abstract"):
                # Strip tags
                meta["text_content"] = _re.sub(r"<[^>]+>", "", data["abstract"]).strip()
            return meta
        except Exception as exc:  # noqa: BLE001
            logger.debug("Crossref fetch failed for {} - {}", doi, exc)
            return {}

    def _fetch_semantic_scholar_arxiv(self, arxiv_id: str, timeout: int = 10) -> dict[str, Any]:
        """Secondary arXiv metadata via Semantic Scholar - currently stubbed."""
        return {}


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
