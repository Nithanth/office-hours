#!/usr/bin/env python
"""Quick CLI to inspect each TextExtractor layer against one or more URLs.

Example
-------
$ poetry run python scripts/test_text_extraction.py https://arxiv.org/pdf/2502.09992

It will attempt every extraction layer in order and print whether it succeeded,
its output length, or the reason for failure/empty content.
"""

from __future__ import annotations

import asyncio
import re  # for safe filename generation
import sys
import traceback  # for printing exceptions inside diagnostics
from argparse import ArgumentParser
from collections.abc import Awaitable
from pathlib import Path
from textwrap import shorten
from typing import Callable

import aiohttp
from loguru import logger

# Ensure project root is on sys.path when executed from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Add ./src to import path so "services.*" resolves when project uses src layout
SRC_DIR = ROOT / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from services.paper.text_extractor import TextExtractor
except ModuleNotFoundError as exc:  # pragma: no cover
    print("Error: could not import TextExtractor. Run from repository root.")
    raise SystemExit(1) from exc


async def _debug_url(
    url: str,
    use_exa: bool,
    *,
    dump: bool = False,
    dump_raw: bool = False,
    out_dir: Path | None = None,
) -> None:
    extractor = TextExtractor.instance()

    # Build layer list while preserving static type correctness (no Nones).
    _raw_layers: list[tuple[str, Callable[[str], Awaitable[str | None]]] | None] = [
        ("arxiv_pdf", extractor._layer_arxiv_pdf),
        ("pdf_generic", extractor._layer_pdf),
        ("html_pdf_link", extractor._layer_html_pdf_link),
        # Exa contents layer is optional.
        ("exa_contents", extractor._layer_exa_contents)
        if use_exa and extractor._layer_exa_contents
        else None,
        ("arxiv_api", extractor._layer_arxiv),
        ("html_readability", extractor._layer_html),
    ]

    layers: list[tuple[str, Callable[[str], Awaitable[str | None]]]] = [
        layer for layer in _raw_layers if layer is not None
    ]

    print(f"\n=== Inspecting: {url}\n")
    for name, func in layers:
        try:
            result = await func(url)
            if result:
                preview = shorten(result.replace("\n", " "), width=80, placeholder="…")
                print(f"[✓] {name:15} {len(result):6,} chars | {preview}")
                if dump and out_dir is not None:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", url)[:60]
                    # Write cleaned text (default behaviour of layer)
                    file_path = out_dir / f"{safe}_{name}_clean.txt"
                    file_path.write_text(result, encoding="utf-8")

                    # Optionally write RAW (un-cleaned) version for PDF layers.
                    if dump_raw and name in {"arxiv_pdf", "pdf_generic", "html_pdf_link"}:
                        # Temporarily disable cleaning by monkey-patching _clean_text
                        from services.paper import text_extractor as _te

                        orig_clean = _te._clean_text  # type: ignore[attr-defined]
                        _te._clean_text = lambda x: x  # type: ignore[assignment]
                        try:
                            raw_res = await func(url)
                            if raw_res:
                                raw_path = out_dir / f"{safe}_{name}_raw.txt"
                                raw_path.write_text(raw_res, encoding="utf-8")
                        finally:
                            _te._clean_text = orig_clean  # type: ignore[assignment]
            else:
                print(f"[ ] {name:15} no content or below quality threshold")
        except Exception as exc:  # pragma: no cover – diagnostics only
            logger.debug("layer_exception", layer=name, url=url, err=str(exc))
            print(f"[✗] {name:15} raised {type(exc).__name__}: {exc}")


async def _trace_pdf(extractor: TextExtractor, url: str) -> None:
    """Run _layer_pdf and print internal debug stats (header bytes, final size, char count)."""
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp,
    ):
        header_bytes = int(resp.headers.get("content-length", 0))
        status = resp.status
        pdf_bytes = await resp.read()

    text = await extractor._layer_pdf(url)
    print("\n--- PDF TRACE ---")
    print(f"status: {status}")
    print(f"Content-Length header: {header_bytes // 1_048_576} MB")
    print(f"Actual bytes: {len(pdf_bytes) // 1_048_576} MB")
    print(f"Chars extracted: {len(text) if text else 0}")
    print("(empty means PyMuPDF + pdfminer both failed)")
    print("------------------\n")

    # Deeper dive: compare PyMuPDF extraction in-thread vs direct call.
    await _trace_pymupdf(pdf_bytes)


# ---------------------------------------------------------------------------
# Extra diagnostics – PyMuPDF direct vs threaded
# ---------------------------------------------------------------------------


async def _trace_pymupdf(pdf_bytes: bytes) -> None:
    """Show whether PyMuPDF behaves differently in main thread vs asyncio.to_thread."""
    from services.paper.text_extractor import (
        _clean_text,
        _extract_with_pymupdf,
    )  # local import to avoid circulars

    print("--- PyMuPDF TRACE ---")
    # Direct call
    try:
        direct = _extract_with_pymupdf(pdf_bytes)
        print(f"direct   : {len(direct)} chars")
    except Exception as exc:
        print(f"direct   : raised {type(exc).__name__}")
        traceback.print_exception(exc)

    # Worker-thread call (same as pipeline)
    try:
        threaded = await asyncio.to_thread(_extract_with_pymupdf, pdf_bytes)
        print(f"threaded : {len(threaded)} chars")
    except Exception as exc:
        print(f"threaded : raised {type(exc).__name__}")
        traceback.print_exception(exc)

    # Clean text
    try:
        clean_direct = _clean_text(direct)
        print(f"clean_direct   : {len(clean_direct)} chars")
    except Exception as exc:
        print(f"clean_direct   : raised {type(exc).__name__}")
        traceback.print_exception(exc)

    try:
        clean_threaded = _clean_text(threaded)
        print(f"clean_threaded : {len(clean_threaded)} chars")
    except Exception as exc:
        print(f"clean_threaded : raised {type(exc).__name__}")
        traceback.print_exception(exc)

    print("-----------------------\n")


async def _main(argv: list[str]) -> None:
    parser = ArgumentParser()
    parser.add_argument("--no-exa", action="store_false", dest="use_exa", help="Skip Exa layer")
    parser.add_argument("--trace-pdf", action="store_true", help="Run low-level PDF trace")
    parser.add_argument(
        "--dump", action="store_true", help="Dump successful layer outputs to files"
    )
    parser.add_argument(
        "--dump-raw",
        action="store_true",
        help="When used with --dump, also dump un-cleaned PDF text (_raw.txt).",
    )
    default_out = Path(__file__).resolve().parent / "text_dumps"
    parser.add_argument(
        "--out",
        default=str(default_out),
        help="Directory for dumps (used with --dump). If relative, it is resolved under the script's directory.",
    )
    parser.add_argument("urls", nargs="+", help="URLs to inspect")
    args = parser.parse_args(argv)

    if args.dump:
        out_dir = Path(args.out)
        if not out_dir.is_absolute():
            out_dir = Path(__file__).resolve().parent / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        args.out = str(out_dir)

    for url in args.urls:
        await _debug_url(
            url,
            args.use_exa,
            dump=args.dump,
            dump_raw=args.dump_raw,
            out_dir=Path(args.out),
        )
        if args.trace_pdf:
            await _trace_pdf(TextExtractor.instance(), url)


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
