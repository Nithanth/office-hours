"""Async layered full-text extractor with on-disk caching."""

import asyncio
import hashlib
import io
import os
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast
from urllib.parse import urljoin

import aiohttp
from loguru import logger
from pdfminer.high_level import extract_text

# Internal helpers
from services.core.exa_utils import url_utils as _url_utils

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

# ---------------------------------------------------------------------------
# PDF extraction configuration (centralised via config.py). Defaults to 30 MB.
# ---------------------------------------------------------------------------
_DEFAULT_MAX_MB = 30
_MAX_PDF_MB = getattr(app_config, "MAX_PDF_MB", _DEFAULT_MAX_MB) if app_config else _DEFAULT_MAX_MB
MAX_PDF_BYTES = _MAX_PDF_MB * 1024 * 1024

# Quality thresholds
_MIN_FULL_TEXT = getattr(app_config, "MIN_FULL_TEXT_CHARS", 3000) if app_config else 3000
_MIN_PARTIAL = getattr(app_config, "MIN_PARTIAL_TEXT_CHARS", 400) if app_config else 400

# OCR settings
_OCR_ENABLED = bool(getattr(app_config, "ENABLE_PDF_OCR", False))
_OCR_MAX_BYTES = (
    getattr(app_config, "OCR_MAX_PDF_MB", 10) * 1024 * 1024 if app_config else 10 * 1024 * 1024
)

# Try to import PyMuPDF (fitz). Falls back to pdfminer only when unavailable or fails.
try:
    import fitz

    def _extract_with_pymupdf(pdf_bytes: bytes) -> str:
        """Return plaintext extracted via PyMuPDF (fast, robust)."""
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text("text") for page in doc)

except ModuleNotFoundError:  # pragma: no cover – optional dep
    fitz = None

    def _extract_with_pymupdf(pdf_bytes: bytes) -> str:  # noqa: D401 – same sig
        """No-op when PyMuPDF missing (caller falls back)."""
        return ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_text(raw: str) -> str:
    """Lightweight cleaning: unwrap lines, strip HTML/JS noise, collapse spaces."""
    txt = textwrap.dedent(raw)

    # Remove <script> / <style> blocks (quick regex – good enough for plain pages)
    txt = re.sub(r"<script[\s\S]*?</script>", " ", txt, flags=re.I)
    txt = re.sub(r"<style[\s\S]*?</style>", " ", txt, flags=re.I)

    # Drop the remaining HTML tags if any
    txt = re.sub(r"<[^>]+>", " ", txt)

    # Collapse multiple blank lines and extra whitespace
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"[ \t]{2,}", " ", txt)

    # Remove leading/trailing spaces on each line
    txt = "\n".join(line.strip() for line in txt.splitlines())

    lines = txt.splitlines()
    n = len(lines)

    ref_idx: int | None = None
    appendix_idx: int | None = None

    for idx, line in enumerate(lines):
        # Heading heuristics apply only in the last half of the doc.
        if idx < n * 0.5:
            continue

        if ref_idx is None and re.match(r"^\s*(references|bibliography)\b[:\s]*$", line, re.I):
            ref_idx = idx
            continue

        # Keep track of an appendix heading *after* references.
        if appendix_idx is None and re.match(r"^\s*(appendix|supplementary)\b", line, re.I):
            appendix_idx = idx

    if ref_idx is not None:
        if appendix_idx is not None and appendix_idx > ref_idx:
            # Drop only the bibliography block between the two headings.
            txt = "\n".join(lines[:ref_idx] + lines[appendix_idx:])
        else:
            # No appendix after references – drop everything from references onward.
            txt = "\n".join(lines[:ref_idx])

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
        # Canonicalise: convert arXiv abs links and other variants to their PDF form.
        # This ensures a unique cache entry and lets the PDF layer succeed early.
        url = _url_utils.upgrade_to_pdf_url(url) or url
        cache_path = self._cache_path(url, paper_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and not force:
            return str(cache_path)

        layers = [
            # Prefer full-text sources first; fall back to lighter layers later.
            self._layer_arxiv_pdf,
            self._layer_pdf,
            self._layer_html_pdf_link,
            self._layer_exa_contents if prefer_exa else None,
            self._layer_arxiv,
            self._layer_html,
        ]

        extracted: str | None = None
        best_partial: str | None = None
        is_partial: bool = False
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
            if not extracted:
                continue

            # Full-length success → stop searching.
            if len(extracted) >= _MIN_FULL_TEXT:
                is_partial = False
                break

            # Otherwise consider partials if allowed for this layer.
            allow_partial = layer.__name__ in {"_layer_arxiv", "_layer_exa_contents"}
            if (
                allow_partial
                and len(extracted) >= _MIN_PARTIAL
                and (best_partial is None or len(extracted) > len(best_partial))
            ):
                best_partial = extracted

            # Reset so later layers can try to get full text.
            extracted = None

        # If no full text was found, fall back to the best partial (if any).
        if extracted is None and best_partial is not None:
            extracted = best_partial
            is_partial = True

        if extracted is None:
            extra = {"url": url}
            if paper_id:
                extra["paper_id"] = paper_id
            logger.info("full_text_failed", **extra)
            return None

        # Determine final cache path (partial extracts get different suffix for downstream awareness)
        try:
            final_path = cache_path
            if is_partial:
                final_path = cache_path.with_suffix(".partial.txt")

            final_path.write_text(extracted, encoding="utf-8")
            # Build RAG vector index asynchronously (best-effort, non-blocking).
            try:
                if paper_id:
                    from services.paper import vector_store as _vs  # local import to avoid cycles

                    if not _vs.index_exists(paper_id):
                        _vs.build_index_from_text(paper_id, extracted)
            except Exception as _exc:  # pragma: no cover
                logger.warning("vector_index_error", paper_id=paper_id, error=str(_exc))

            logger.info(
                "full_text_cached",
                path=str(final_path),
                chars=len(extracted),
                partial=is_partial,
            )
            return str(final_path)
        except Exception as exc:
            logger.error("cache_write_failed", path=str(cache_path), error=str(exc))
            return None

    async def load_full_text(self, paper_id: str) -> str | None:
        """Return absolute cache **file path** for *paper_id* (if any)."""
        # Prefer full extracts over partial abstracts if both exist.
        candidates = sorted(CACHE_ROOT.glob(f"{paper_id}_*.txt"))
        if not candidates:
            return None

        # Full file (no .partial) wins; fallback to partial
        for p in candidates:
            if not p.name.endswith(".partial.txt"):
                return str(p)
        return str(candidates[0])

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
        if not (url.lower().endswith(".pdf") or "/pdf/" in url.lower()):
            return None
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
        ):
            header_bytes = int(resp.headers.get("content-length", 0))
            if header_bytes and header_bytes > MAX_PDF_BYTES * 2:
                logger.info(
                    "pdf_skip_too_large_header",
                    url=url,
                    mb=header_bytes // 1_048_576,
                )
                return None

            pdf_bytes = await resp.read()

            # Final check on the actual bytes received.
            if len(pdf_bytes) > MAX_PDF_BYTES:
                logger.info(
                    "pdf_skip_too_large_dl",
                    url=url,
                    mb=len(pdf_bytes) // 1_048_576,
                )
                return None

        text: str = ""

        # Prefer PyMuPDF when available (much faster, handles most PDFs).
        if fitz is not None:
            try:
                text = await asyncio.to_thread(_extract_with_pymupdf, pdf_bytes)
                print(f"pymu_text_len: {len(text)}")
            except Exception as pymu_exc:  # pragma: no cover – robustness
                print(f"pymupdf_failed: {pymu_exc}")  # DEBUG
                logger.debug("pymupdf_failed", url=url, err=str(pymu_exc))

        # Fallback to pdfminer if PyMuPDF unavailable or returned empty.
        if not text.strip():
            try:
                text = await asyncio.to_thread(extract_text, io.BytesIO(pdf_bytes))
            except Exception as miner_exc:
                logger.debug("pdfminer_failed", url=url, err=str(miner_exc))

        # OCR fallback for image-only PDFs
        if (
            not text.strip()
            and _OCR_ENABLED
            and len(pdf_bytes) <= _OCR_MAX_BYTES
            and fitz is not None
        ):
            try:
                text = await asyncio.to_thread(self._ocr_pdf, pdf_bytes)
            except Exception as ocr_exc:
                logger.debug("ocr_failed", url=url, err=str(ocr_exc))

        cleaned_text = _clean_text(text)
        print(f"cleaned_len: {len(cleaned_text)}")
        return cleaned_text or None

    @staticmethod
    def _ocr_pdf(pdf_bytes: bytes) -> str:
        """Run Tesseract OCR via PyMuPDF page renderings - heavy but last resort."""
        import pytesseract  # imported lazily to avoid unconditional dependency
        from PIL import Image  # Pillow

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            texts: list[str] = []
            for page in doc:
                # Render page at 300 DPI for better accuracy
                pix = page.get_pixmap(dpi=300)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                texts.append(pytesseract.image_to_string(img))
        return "\n".join(texts)

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
            # Replace any char other than safe set with underscore *including* slashes
            safe_fragment = re.sub(r"[^A-Za-z0-9._-]", "_", paper_id)[:80]
            # As an extra guard, strip path separators that could appear after re.sub on exotic unicode
            safe_fragment = safe_fragment.replace(os.sep, "_").replace("/", "_")
        filename = f"{safe_fragment}_{key_hash}.txt" if safe_fragment else f"{key_hash}.txt"
        return CACHE_ROOT / filename

    @staticmethod
    def _good_quality(text: str | None, *, partial_ok: bool = False) -> bool:
        """Return ``True`` when *text* meets quality thresholds.

        Full extracts must have ≥ ``_MIN_FULL_TEXT`` characters. If *partial_ok*
        is ``True`` we accept shorter excerpts (e.g. abstract) as long as they
        meet ``_MIN_PARTIAL``. This prevents discarding valuable abstracts when
        no PDF text is available.
        """
        if not text:
            return False
        if len(text) >= _MIN_FULL_TEXT:
            return True
        return partial_ok and len(text) >= _MIN_PARTIAL
