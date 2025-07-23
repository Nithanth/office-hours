"""Async layered full-text extractor with on-disk caching."""

import asyncio
import hashlib
import io
import json
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
        """Return plaintext extracted via PyMuPDF with simple two-column fix.

        PyMuPDF sometimes interleaves left/right columns when reading newspaper-style
        layouts.  To reduce this we fetch *blocks* for each page, then sort primarily
        by the *y* coordinate and secondarily by *x* (so left column lines precede
        right column lines on the same row).
        """
        all_pages: list[str] = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                try:
                    blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, ...)
                except Exception:
                    # Fall back to default ordering when blocks extraction fails.
                    all_pages.append(page.get_text("text"))
                    continue

                # Sort by rounded y (to bucket lines) then x.
                blocks_sorted = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
                page_lines: list[str] = []
                for b in blocks_sorted:
                    txt = b[4].rstrip()
                    if txt:
                        page_lines.append(txt)
                all_pages.append("\n".join(page_lines))

        return "\n".join(all_pages)

except ModuleNotFoundError:  # pragma: no cover – optional dep
    fitz = None

    def _extract_with_pymupdf(pdf_bytes: bytes) -> str:  # noqa: D401 – same sig
        """No-op when PyMuPDF missing (caller falls back)."""
        return ""


# ---------------------------------------------------------------------------
# Optional table extraction support via *pdfplumber* (pure-Python, lightweight).
# We try to import it lazily; if unavailable, table extraction is skipped
# without breaking the main text flow.
# ---------------------------------------------------------------------------
try:
    import pandas as _pd  # noqa: N812 – keep short alias
    import pdfplumber

    def _extract_tables(pdf_bytes: bytes) -> str:
        """Return all detected tables as Markdown concatenated with blank lines.

        Each table is prefixed with a sentinel so RAG consumers can identify
        them easily.  We keep the conversion lightweight (Markdown) so the
        chunks render nicely in chat UIs.
        """

        import io
        import textwrap as _tw

        out: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as _pdf:
            for page_idx, page in enumerate(_pdf.pages, start=1):
                try:
                    tables = page.extract_tables()
                except Exception:  # pragma: no cover – rare parsing errors
                    continue

                for tbl_idx, tbl in enumerate(tables, start=1):
                    try:
                        df = _pd.DataFrame(tbl)
                    except Exception:
                        continue
                    md = df.to_markdown(index=False)
                    header = f"=== TABLE page {page_idx} #{tbl_idx} ==="
                    out.append(f"{header}\n{_tw.dedent(md)}")

        return "\n\n".join(out)

except ModuleNotFoundError:  # pragma: no cover – optional dep missing

    def _extract_tables(pdf_bytes: bytes) -> str:
        """Dummy fallback when *pdfplumber* not installed."""

        return ""


# ---------------------------------------------------------------------------
# Figure caption extraction – lightweight regex over raw text
# ---------------------------------------------------------------------------
FIGURE_CAP_RE = re.compile(r"^(?:Figure|Fig\.)\s*\d+[:\.\-]?", re.IGNORECASE)


def _extract_fig_captions(raw_text: str) -> str:
    """Return figure captions as Markdown-prefixed blocks.

    We scan the *raw* (pre-clean) text for lines starting with
    "Figure 3:" / "Fig. 4." etc.  For each hit we grab the caption line plus
    up to two subsequent non-blank lines (authors often wrap captions).
    Each block is prefixed with `=== FIGURE ===` so downstream RAG steps can
    identify and boost them.
    """

    out: list[str] = []
    lines = raw_text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if FIGURE_CAP_RE.match(ln):
            cap_lines = [ln]
            for j in range(1, 3):
                if i + j >= len(lines):
                    break
                nxt = lines[i + j].strip()
                if not nxt or FIGURE_CAP_RE.match(nxt):
                    break
                cap_lines.append(nxt)
            out.append("=== FIGURE ===\n" + " ".join(cap_lines))
        i += 1
    return "\n\n".join(out)


def _extract_pdf_metadata(pdf_bytes: bytes) -> dict[str, str]:
    """Return minimal metadata dict {title, authors, year} (best-effort)."""

    meta: dict[str, str] = {}

    # Prefer PyMuPDF XMP metadata when available.
    if fitz is not None:
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as _doc:
                info = _doc.metadata or {}
                if info.get("title"):
                    meta["title"] = info["title"].strip()
                if info.get("author"):
                    meta["authors"] = info["author"].strip()
                if info.get("creationDate") and info["creationDate"].startswith("D:"):
                    meta["year"] = info["creationDate"][2:6]
        except Exception:
            pass

    # Fallback heuristic: use first 20 lines of extracted text
    if not meta.get("title") or not meta.get("authors"):
        try:
            raw_first_page = _extract_with_pymupdf(pdf_bytes)[:4000]
            lines = [ln.strip() for ln in raw_first_page.splitlines() if ln.strip()]
            if not meta.get("title") and lines:
                meta["title"] = lines[0][:300]
            # Authors line often contains commas and no longer than 200 chars
            if not meta.get("authors"):
                for ln in lines[1:6]:
                    if "," in ln and len(ln) < 200:
                        meta["authors"] = ln
                        break
        except Exception:
            pass

    return meta


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

    # ---------------------------------------------------------------
    # Simple & safe bibliography detection:
    #   1. Scan upward through the bottom 40 % of the document for a line
    #      that starts with "References" or "Bibliography" (case-insensitive).
    #   2. Verify that at least 3 of the next 10 lines look like citation
    #      entries (numbered + author/year). If so, drop everything from the
    #      heading onward. Otherwise ignore the heading (likely a false hit).
    # ---------------------------------------------------------------
    heading_re = re.compile(r"^\s*(references|bibliography)\b", re.I)
    cite_re = re.compile(r"^\s*(\[?\d+\]?|\d+\.)\s+")

    # Honour environment flag – default *false* so we keep references unless
    # explicitly opted-in.  This avoids losing body text and lets the RAG layer
    # filter citations later.
    strip_refs = getattr(app_config, "STRIP_REFERENCES", False) if app_config else False

    scan_start = int(n * 0.6)  # bottom 40 %
    for idx in range(n - 1, scan_start - 1, -1):
        if heading_re.match(lines[idx]):
            sample = lines[idx + 1 : idx + 11]
            cites = sum(1 for line in sample if cite_re.match(line))
            if cites >= 3:
                ref_idx = idx
                break

    if strip_refs and ref_idx is not None:
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
            # Fast path: text already cached. Ensure the vector index exists;
            # it may have been purged separately via `flush_caches --vector`.
            if paper_id:
                try:
                    from services.paper import vector_store as _vs  # local import to avoid cycles

                    if not _vs.index_exists(paper_id):
                        try:
                            cached_text = cache_path.read_text(encoding="utf-8")
                        except OSError:
                            cached_text = ""

                        if cached_text:
                            _vs.build_index_from_text(paper_id, cached_text)
                            if url != paper_id and not _vs.index_exists(url):
                                _vs.build_index_from_text(url, cached_text)
                            logger.info(
                                "vector_index_rebuilt", paper_id=paper_id, chars=len(cached_text)
                            )
                except Exception as _exc:  # pragma: no cover
                    logger.warning("vector_index_error", paper_id=paper_id, error=str(_exc))

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

            # Full-length success
            if len(extracted) >= _MIN_FULL_TEXT:
                # Exa get_contents can exceed _MIN_FULL_TEXT (~7k chars) but is still
                # much shorter than the full PDF. Treat it as *partial* so the
                # extractor keeps looking for richer sources that may follow.
                if layer.__name__ == "_layer_exa_contents":
                    # Track as best partial and continue to next layer.
                    if best_partial is None or len(extracted) > len(best_partial):
                        best_partial = extracted
                    extracted = None
                    # Do NOT update is_partial here; we may still find full text.
                    continue

                # Any other layer reaching the threshold counts as full text.
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

            # ------- save metadata JSON (best-effort) -------
            try:
                meta: dict[str, str] = {}
                if url.lower().endswith(".pdf"):

                    async def _meta_task() -> None:
                        """Background task to download the PDF and parse metadata."""
                        import requests  # type: ignore[import-untyped]  # local import to avoid mandatory dep

                        nonlocal meta
                        try:
                            resp = requests.get(url, timeout=15)
                            if resp.ok and resp.headers.get("content-type", "").startswith(
                                "application/pdf"
                            ):
                                meta = _extract_pdf_metadata(resp.content)
                        except Exception as _req_exc:  # pragma: no cover – best-effort
                            logger.debug("meta_dl_failed", err=str(_req_exc))

                    await _meta_task()
                if meta:
                    meta_path = Path(str(final_path).replace(".txt", ".meta.json"))
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            except Exception as _meta_exc:
                logger.debug("metadata_write_failed", err=str(_meta_exc))

            # Build RAG vector index asynchronously (best-effort, non-blocking).
            try:
                if paper_id:
                    from services.paper import vector_store as _vs  # local import to avoid cycles

                    if not _vs.index_exists(paper_id):
                        _vs.build_index_from_text(paper_id, extracted)
                    if url != paper_id and not _vs.index_exists(url):
                        _vs.build_index_from_text(url, extracted)
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

        # ---------------- conditional fetch ----------------
        headers: dict[str, str] | None = None
        import config as _cfg

        if _cfg.config.USE_COND_FETCH:
            import hashlib as _hashlib
            from email.utils import formatdate

            # Use mtime of previously cached text (if any) to build HTTP-date.
            h = _hashlib.sha1(url.encode()).hexdigest()[:16]
            try:
                existing = next(CACHE_ROOT.glob(f"*_{h}.txt"))
                mtime = existing.stat().st_mtime
                headers = {"If-Modified-Since": formatdate(mtime, usegmt=True)}
            except StopIteration:
                pass  # no existing file ⇒ full download

        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers=headers,  # may be None
            ) as resp,
        ):
            # Short-circuit when server signals resource unchanged.
            if resp.status == 304:
                logger.debug("pdf_not_modified", url=url)
                return None

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
                logger.debug("pymu_text_len", chars=len(text))
            except Exception as pymu_exc:  # pragma: no cover – robustness
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

        # Append any tables we can extract (if pdfplumber present).
        try:
            tables_md = await asyncio.to_thread(_extract_tables, pdf_bytes)
            if tables_md.strip():
                text = f"{text}\n\n{tables_md}"
                logger.debug("tables_extracted", count=tables_md.count("=== TABLE"))
        except Exception as _tbl_exc:  # pragma: no cover – table extraction is best-effort
            logger.debug("table_extract_failed", err=str(_tbl_exc))

        # Append detected figure captions (best-effort).
        try:
            figs_md = _extract_fig_captions(text)
            if figs_md.strip():
                text = f"{text}\n\n{figs_md}"
                logger.debug("figures_extracted", count=figs_md.count("=== FIGURE"))
        except Exception as _fig_exc:  # pragma: no cover – robustness first
            logger.debug("figure_extract_failed", err=str(_fig_exc))

        cleaned_text = _clean_text(text)
        logger.debug("pdf_cleaned_text_chars", chars=len(cleaned_text))
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

    async def load_metadata(self, paper_id: str) -> dict[str, Any] | None:
        """Return cached metadata dict for *paper_id* if present."""

        path_str = await self.load_full_text(paper_id)
        if not path_str:
            return None
        meta_path = Path(path_str).with_suffix(".meta.json")
        if not meta_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            return None
