import asyncio
from typing import Any, Optional

from loguru import logger

from models.paper import Paper
from services.core.exa_client import ExaClient
from services.core.exa_utils import merge_utils as _merge_utils
from services.core.exa_utils import paper_utils as _paper_utils
from services.core.exa_utils import url_utils as _url_utils

"""Async orchestrator that merges metadata from arXiv, DOI providers, and Exa."""


class MetadataEnricher:
    """High-level async orchestrator that layers multiple metadata sources.

    Trust order (highest → lowest):
        1. arXiv official API
        2. Semantic Scholar (DOI) / Crossref
        3. Exa search/contents
    """

    # Per-API global semaphores (coarse - adjust as needed)
    _arxiv_sem = asyncio.Semaphore(1)  # arXiv polite limit ≈1 req / 3 s
    _ss_sem = asyncio.Semaphore(3)  # SemanticScholar anonymous ≈100/day
    _exa_sem = asyncio.Semaphore(20)  # Exa generous

    def __init__(self, exa_client: ExaClient):
        self.client = exa_client

    # Public API ---------------------------------------------------------
    async def enrich(self, url: str) -> Paper | None:
        """Return best-effort enriched Paper object (non-blocking).

        The first authoritative source that yields valid metadata wins; lower
        tiers fill the remaining holes without overriding existing values.
        """
        loop = asyncio.get_running_loop()

        arxiv_id = _url_utils.extract_arxiv_id(url)
        doi_val = _url_utils.extract_doi(url)

        # Schedule tasks lazily
        tasks: list[asyncio.Task] = []

        if arxiv_id:
            tasks.append(loop.create_task(self._fetch_arxiv(arxiv_id)))
        if doi_val:
            tasks.append(loop.create_task(self._fetch_semantic(doi_val)))
            tasks.append(loop.create_task(self._fetch_crossref(doi_val)))
        # Always have an Exa task as fallback (search/contents auto-handles arXiv/others)
        tasks.append(loop.create_task(self._fetch_exa(url, arxiv_id)))

        # Gather with return_exceptions=True so one failure doesn’t cancel others
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Merge according to trust priority defined above
        meta_final: dict[str, Any] = {}
        for meta in results:  # already ordered by schedule priority
            if isinstance(meta, dict):
                meta_final = _merge_utils.merge_dicts_keep_first(meta, meta_final)

        if not meta_final:
            return None

        # Build Paper object (id/url must exist)
        pid = meta_final.get("id") or url
        paper = Paper(
            id=pid,
            title=meta_final.get("title") or "No Title Provided",
            url=_paper_utils._coerce_http_url(url),
        )
        paper = _paper_utils.update_paper_from_meta(paper, meta_final)
        return paper

    # Internal async wrappers -------------------------------------------
    async def _fetch_arxiv(self, arxiv_id: str) -> dict[str, Any]:
        async with self._arxiv_sem:
            logger.debug("[enrich] arXiv fetch for {}", arxiv_id)
            return await asyncio.to_thread(self.client.fetch_arxiv_metadata, arxiv_id)

    async def _fetch_semantic(self, doi: str) -> dict[str, Any]:
        async with self._ss_sem:
            logger.debug("[enrich] SemanticScholar fetch for {}", doi)
            return await asyncio.to_thread(self.client.fetch_doi_metadata, doi)

    async def _fetch_crossref(self, doi: str) -> dict[str, Any]:
        # Crossref allows generous quota; share ss_sem
        async with self._ss_sem:
            logger.debug("[enrich] Crossref fetch for {}", doi)
            return await asyncio.to_thread(self.client.fetch_crossref_metadata, doi)

    async def _fetch_exa(self, url: str, arxiv_id: Optional[str]) -> dict[str, Any]:
        async with self._exa_sem:
            logger.debug("[enrich] Exa fetch for {}", url)
            # Use new logic inside client (handles pdf→abs fallback)
            tmp_paper = Paper(id=url, title="No Title", url=_paper_utils._coerce_http_url(url))
            tmp_paper = await asyncio.to_thread(self.client._enrich_paper_metadata, tmp_paper)
            # Convert to flat meta dict for merge
            return {
                "title": tmp_paper.title,
                "authors": tmp_paper.authors,
                "published_date": tmp_paper.published_date,
                "text_content": tmp_paper.text_content,
            }
