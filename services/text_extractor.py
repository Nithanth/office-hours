"""services/text_extractor.py

Lightweight asynchronous full-text extraction and caching layer.
This module follows a *layered* strategy (cache → Exa contents →
arXiv API → direct PDF → readability HTML) to retrieve the best
possible plaintext representation of a research paper.

The extractor is intentionally self-contained so it can be imported
by ExaClient and ReportGenerator without causing heavy imports unless
used.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin

# Third-party I/O libraries used in extraction layers
import aiohttp
from pdfminer.high_level import extract_text

# ---------------------------------------------------------------------------
# Import readability.Document with graceful fallback and mypy-friendly stubs
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    # During static type checking we don't care about implementation – use Any.
    Document = Any
else:
    try:
        from readability import Document  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover – fallback path
        try:
            from readability_lxml import Document  # type: ignore[import-not-found]
        except ModuleNotFoundError:  # pragma: no cover – final stub fallback

            class Document:  # type: ignore[too-few-public-methods]
                """Minimal stub replacement when readability packages are absent."""

                def __init__(self, html: str) -> None:
                    self._html = html

                def summary(self, html_partial: bool = True) -> str:  # noqa: D401
                    return self._html


# Local import – keep inside try/except so text_extractor is usable in
# unit tests without real API keys.
try:
    from config import config as app_config
    from services.exa_client import ExaClient  # Circular import safe – used lazily
except Exception:  # pragma: no cover – during lint/CI without deps
    app_config = None  # type: ignore[assignment]
    ExaClient = cast(Any, None)  # type: ignore[misc]

logger = logging.getLogger(__name__)

CACHE_ROOT = Path("data/full_text")
CACHE_ROOT.mkdir(parents=True, exist_ok=True)

# Increased size cap for PDFs – many conference papers are >10 MB
MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MB

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(raw: str) -> str:
    """Very small cleaning pass: unwrap long lines and collapse spaces."""
    txt = textwrap.dedent(raw)
    # Collapse multiple blank lines
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    # Remove leading/trailing spaces on each line
    txt = "\n".join(line.strip() for line in txt.splitlines())
    return txt.strip()


# ---------------------------------------------------------------------------
# Extractor class
# ---------------------------------------------------------------------------


class TextExtractor:
    """Asynchronous, cached text extraction for paper URLs."""

    _instance: TextExtractor | None = None

    # Singleton factory --------------------------------------------------
    @classmethod
    def instance(cls) -> TextExtractor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Public API ----------------------------------------------------------

    async def ensure_cached(
        self,
        url: str,
        paper_id: str | None = None,
        *,
        force: bool = False,
        prefer_exa: bool = True,
    ) -> str | None:
        """Idempotently create a cached full-text file for *url*.

        Returns absolute path to the text file, or ``None`` if every
        extraction layer failed.  *force* bypasses the existing cache.
        """
        cache_path = self._cache_path(url, paper_id)
        if cache_path.exists() and not force:
            return str(cache_path)

        # Layered extraction --------------------------------------------
        layers = [
            self._layer_exa_contents if prefer_exa else None,
            self._layer_arxiv_pdf,
            self._layer_pdf,
            self._layer_html_pdf_link,
            self._layer_arxiv,
            self._layer_html,
        ]

        extracted: str | None = None
        for layer in layers:
            if layer is None:
                continue
            try:
                extracted = await layer(url)
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s failed for %s – %s", layer.__name__, url, exc)
                continue
            if self._good_quality(extracted):
                break  # Success!
            extracted = None  # reset for next layer

        if extracted is None:
            logger.warning("Full-text extraction failed for %s", url)
            return None

        try:
            cache_path.write_text(extracted, encoding="utf-8")
            logger.info("Full-text cached ➜ %s (%d chars)", cache_path, len(extracted))
            return str(cache_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not write cache file %s – %s", cache_path, exc)
            return None

    async def load_full_text(self, paper_id: str) -> str | None:
        """Return absolute cache **file path** for *paper_id* (if any)."""
        path = next(CACHE_ROOT.glob(f"{paper_id}_*.txt"), None)
        return str(path) if path and path.exists() else None

    async def read_full_text(self, paper_id: str) -> str | None:
        """Read and return cached plaintext for *paper_id*.  Returns ``None`` if missing."""
        path_str = await self.load_full_text(paper_id)
        if not path_str:
            return None
        try:
            return Path(path_str).read_text(encoding="utf-8")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Compatibility helper expected by chat_stream endpoint
    # ------------------------------------------------------------------

    async def get_paper_content_from_id(self, paper_id: str) -> str | None:  # noqa: D401
        """Return (and ensure) cached plaintext path for *paper_id*.

        The chat streaming endpoint historically expects a *paper_service*
        with ``get_paper_content_from_id``.  For now we treat the
        ``paper_id`` parameter as the canonical URL of the paper and simply
        delegate to :meth:`ensure_cached`.

        Args:
            paper_id: Paper URL (e.g. https://arxiv.org/abs/1234.5678).

        Returns:
            Absolute path of the cached plaintext file, or ``None`` if the
            extraction pipeline failed.
        """

        # Fast-path: already cached?
        existing = await self.load_full_text(paper_id)
        if existing:
            return existing

        # Otherwise kick off extraction (prefer Exa contents when available)
        return await self.ensure_cached(paper_id, paper_id=paper_id)

    # ------------------------------------------------------------------
    # Individual extraction layers
    # ------------------------------------------------------------------

    async def _layer_exa_contents(self, url: str) -> str | None:
        """Fetch Exa get_contents(text=True)."""
        if ExaClient is None or app_config is None or not app_config.EXA_API_KEY:
            return None
        client = ExaClient(api_key=app_config.EXA_API_KEY)
        try:
            response = await asyncio.to_thread(client.client.get_contents, [url], text=True)
            if response and response.results:
                raw = response.results[0].text or ""
                return _clean_text(raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Exa get_contents failed for %s – %s", url, exc)
        return None

    async def _layer_arxiv(self, url: str) -> str | None:
        match = re.search(r"(?:arxiv.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5})(?:v\d+)?", url)
        if not match:
            return None
        arxiv_id = match.group(1)
        api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
        async with (
            aiohttp.ClientSession() as session,
            session.get(  # noqa: SIM117
                api_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp,
        ):
            txt = await resp.text()
        # Very naive XML -> text extraction (title + abstract inside <summary>)
        summary_match = re.search(r"<summary>(.*?)</summary>", txt, re.S)
        if summary_match:
            return _clean_text(summary_match.group(1))
        return None

    async def _layer_arxiv_pdf(self, url: str) -> str | None:
        """If *url* is an arXiv abstract page, try its direct PDF."""
        m = re.match(r"https?://arxiv\.org/abs/(?P<id>\d{4}\.\d{4,5})(?:v\d+)?", url)
        if not m:
            return None
        pdf_url = f"https://arxiv.org/pdf/{m.group('id')}.pdf"
        return await self._layer_pdf(pdf_url)

    async def _layer_html_pdf_link(self, url: str) -> str | None:
        """Fetch HTML, look for the first hyperlink ending in .pdf, then extract."""
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp,
        ):
            if resp.content_type != "text/html":
                return None
            raw_html = await resp.text()
        match = re.search(
            r"href=[\'\"](?P<link>[^\'\"]+\.pdf)(?:[?#][^\'\"]*)?[\'\"]", raw_html, re.I
        )
        if not match:
            return None
        pdf_link = match.group("link")
        # Handle relative links
        pdf_link = urljoin(url, pdf_link)
        return await self._layer_pdf(pdf_link)

    async def _layer_pdf(self, url: str) -> str | None:
        if not url.lower().endswith(".pdf"):
            return None
        async with (
            aiohttp.ClientSession() as session,
            session.get(  # noqa: SIM117
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp,
        ):
            if resp.status != 200 or int(resp.headers.get("content-length", 0)) > MAX_PDF_BYTES:
                return None
            pdf_bytes = await resp.read()
        if len(pdf_bytes) > MAX_PDF_BYTES:
            return None
        # pdfminer is CPU-heavy – run in a worker thread. Provide a file-like object.
        text = await asyncio.to_thread(extract_text, io.BytesIO(pdf_bytes))
        return _clean_text(text)

    async def _layer_html(self, url: str) -> str | None:
        async with (
            aiohttp.ClientSession() as session,
            session.get(  # noqa: SIM117
                url, timeout=aiohttp.ClientTimeout(total=20)
            ) as resp,
        ):
            if resp.content_type != "text/html":
                return None
            raw_html = await resp.text()
        readable = Document(raw_html)
        main_html = readable.summary(html_partial=True)
        # crude strip tags
        text = re.sub(r"<[^>]+>", " ", main_html)
        return _clean_text(text)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cache_path(self, url: str, paper_id: str | None) -> Path:
        """Return a filesystem-safe cache path for *url* (and optional *paper_id*).

        The filename embeds a SHA-256 of the URL for uniqueness but also keeps a
        human-readable fragment based on *paper_id* when available.  Because a
        paper_id is often the raw URL itself (containing slashes and other
        illegal path characters), we aggressively **sanitize** it so the
        resulting filename never creates unintended sub-directories.
        """
        key_hash = _sha256(url)

        safe_fragment = ""
        if paper_id:
            # Replace all characters that are not safe for filenames with "_".
            # This includes "/" so we don't accidentally create nested dirs.
            safe_fragment = re.sub(r"[^A-Za-z0-9._-]", "_", paper_id)
            # Prevent exceedingly long filenames on some filesystems
            safe_fragment = safe_fragment[:80]

        filename = f"{safe_fragment}_{key_hash}.txt" if safe_fragment else f"{key_hash}.txt"
        return CACHE_ROOT / filename

    @staticmethod
    def _good_quality(text: str | None) -> bool:
        return bool(text and len(text) >= 3_000)
