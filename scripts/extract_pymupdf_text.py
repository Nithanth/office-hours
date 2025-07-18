#!/usr/bin/env python3
"""Simple CLI to fetch a PDF (HTTP URL or local file) and dump plaintext via PyMuPDF.

Usage:
    python scripts/extract_pymupdf_text.py https://arxiv.org/pdf/2002.10099
    python scripts/extract_pymupdf_text.py /path/to/local.pdf > out.txt

This bypasses the full TextExtractor pipeline and extracts *all* text that
PyMuPDF can see, without quality thresholds or size caps.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError as exc:  # pragma: no cover – optional dep
    print("PyMuPDF (fitz) is not installed; install via `pip install pymupdf`.")
    raise SystemExit(1) from exc


async def _download(url: str) -> bytes:
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp,
    ):
        resp.raise_for_status()
        return await resp.read()


def _extract(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text("text") for page in doc)


def _smart_load(src: str) -> bytes:
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https"}:
        return asyncio.run(_download(src))

    path = Path(src).expanduser()
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        raise SystemExit(1)
    return path.read_bytes()


# Where extracted plaintext files are stored
OUTPUT_DIR = Path(__file__).parent / "text_dumps"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        print(textwrap.dedent(__doc__).strip())
        raise SystemExit(0)

    src = sys.argv[1]
    pdf_bytes = _smart_load(src)
    text = _extract(pdf_bytes)

    # Derive a safe filename from the source URL or path
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https"}:
        stem = Path(parsed.path).stem or "download"
    else:
        stem = Path(src).stem

    out_path = OUTPUT_DIR / f"{stem}.txt"
    out_path.write_text(text, encoding="utf-8")
    print(
        f"[✓] Extracted text saved to {out_path.relative_to(Path(__file__).parent)} ({len(text)} chars)"
    )


if __name__ == "__main__":
    main()
