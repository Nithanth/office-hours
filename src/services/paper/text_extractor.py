"""Async layered full-text extractor with on-disk caching."""

import asyncio
import hashlib
import io
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast
from urllib.parse import urljoin

import aiohttp
from loguru import logger
from pdfminer.high_level import extract_text

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
                """Very small fallback when *readability* libs are missing."""

                def __init__(self, html: str) -> None:
                    self._html = html

                def summary(self, html_partial: bool = True) -> str:
                    return self._html


try:
    from config import config as app_config
    from services.core.exa_client import ExaClient  # Circular import safe – used lazily
except Exception:  # pragma: no cover – during lint/CI without deps
    app_config = None
    ExaClient = cast(Any, None)

CACHE_ROOT = Path("data/full_text")
CACHE_ROOT.mkdir(parents=True, exist_ok=True)

# Increased size cap for PDFs – many conference papers are >10 MB
MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MB


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


class TextExtractor:
    """Asynchronous, cached text extraction for paper URLs."""

    _instance: Optional["TextExtractor"] = None

    @classmethod
    def instance(cls) -> "TextExtractor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_cached(
        self,
        url: str,
        paper_id: str | None = None,
        *,
        force: bool = False,
        prefer_exa: bool = True,
    ) -> str | None:
        """Idempotently create a cached full-text file for *url*.

        Returns absolute path to the text file, or ``None`` if every extraction
        layer failed. *force* bypasses the existing cache.
        """
        cache_path = self._cache_path(url, paper_id)
        if cache_path.exists() and not force:
            return str(cache_path)

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
            except Exception as exc:  # pragma: no cover – network issues
                logger.debug(
                    "layer failed",
                    layer=layer.__name__,
                    url=url,
                    error=str(exc),
                )
                continue
            if self._good_quality(extracted):
                break  # Success!
            extracted = None  # reset for next layer

        if extracted is None:
            logger.warning("Full-text extraction failed", url=url)
            return None

        try:
            cache_path.write_text(extracted, encoding="utf-8")
            logger.info(
                "Full-text cached",
                path=str(cache_path),
                chars=len(extracted),
            )
            return str(cache_path)
        except Exception as exc:
            logger.error("Cache write failed", path=str(cache_path), error=str(exc))
            return None

    async def load_full_text(self, paper_id: str) -> str | None:
        """Return absolute cache **file path** for *paper_id* (if any)."""
        path = next(CACHE_ROOT.glob(f"{paper_id}_*.txt"), None)
        return str(path) if path and path.exists() else None

    async def read_full_text(self, paper_id: str) -> str | None:
        """Read and return cached plaintext for *paper_id*. Returns ``None`` if missing."""
        path_str = await self.load_full_text(paper_id)
        if not path_str:
            return None
        try:
            return Path(path_str).read_text(encoding="utf-8")
        except Exception:
            return None

    async def get_paper_content_from_id(self, paper_id: str) -> str | None:
        """Return (and ensure) cached plaintext path for *paper_id*.

        The chat streaming endpoint historically expects a *paper_service* with
        ``get_paper_content_from_id``. For now we treat the ``paper_id`` parameter as
        the canonical URL of the paper and simply delegate to :meth:`ensure_cached`.
        """
        # Fast-path: already cached?
        existing = await self.load_full_text(paper_id)
        if existing:
            return existing
        # Otherwise kick off extraction (prefer Exa contents when available)
        return await self.ensure_cached(paper_id, paper_id=paper_id)

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
        except Exception as exc:
            logger.debug("Exa get_contents failed", url=url, error=str(exc))
        return None

    async def _layer_arxiv(self, url: str) -> str | None:
        match = re.search(r"(?:arxiv.org/(?:abs|pdf)/|^)(\d{4}\.\d{4,5})(?:v\d+)?", url)
        if not match:
            return None
        arxiv_id = match.group(1)
        api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}&max_results=1"
        async with (
            aiohttp.ClientSession() as session,
            session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            txt = await resp.text()
        # Very naive XML → text extraction (title + abstract inside <summary>)
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
        pdf_link = urljoin(url, pdf_link)  # handle relative link
        return await self._layer_pdf(pdf_link)

    async def _layer_pdf(self, url: str) -> str | None:
        if not url.lower().endswith(".pdf"):
            return None
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
        ):
            if resp.status != 200 or int(resp.headers.get("content-length", 0)) > MAX_PDF_BYTES:
                return None
            pdf_bytes = await resp.read()
        if len(pdf_bytes) > MAX_PDF_BYTES:
            return None
        # pdfminer is CPU-heavy – run in a worker thread.
        text = await asyncio.to_thread(extract_text, io.BytesIO(pdf_bytes))
        return _clean_text(text)

    async def _layer_html(self, url: str) -> str | None:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp,
        ):
            if resp.content_type != "text/html":
                return None
            raw_html = await resp.text()
        readable = Document(raw_html)
        main_html = readable.summary(html_partial=True)
        text = re.sub(r"<[^>]+>", " ", main_html)  # crude strip tags
        return _clean_text(text)

    def _cache_path(self, url: str, paper_id: str | None) -> Path:
        """Return a filesystem-safe cache path for *url* (and optional *paper_id*)."""
        key_hash = _sha256(str(url))
        safe_fragment = ""
        if paper_id:
            safe_fragment = re.sub(r"[^A-Za-z0-9._-]", "_", paper_id)[:80]
        filename = f"{safe_fragment}_{key_hash}.txt" if safe_fragment else f"{key_hash}.txt"
        return CACHE_ROOT / filename

    @staticmethod
    def _good_quality(text: str | None) -> bool:
        """Heuristic: accept *text* if ≥3 kB after cleaning."""
        return bool(text and len(text) >= 3_000)
