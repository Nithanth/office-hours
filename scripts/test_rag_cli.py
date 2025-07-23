#!/usr/bin/env python3
"""
Test RAG functionality with LanceDB vector store.

Example usage:
  # Index a PDF and search it
  python scripts/test_rag_cli.py index /path/to/paper.pdf --paper-id test_paper
  python scripts/test_rag_cli.py search test_paper "your query here"

  # List indexed papers or delete an index
  python scripts/test_rag_cli.py list
  python scripts/test_rag_cli.py delete test_paper

  # Download and index a PDF from URL
  python scripts/test_rag_cli.py index-url https://example.com/paper.pdf

  # Interactive retrieval chat
  python scripts/test_rag_cli.py chat test_paper
  python scripts/test_rag_cli.py chat-url https://example.com/paper.pdf
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

# Ensure project and src directories are on PYTHONPATH so `import services.*` works
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"

for _pth in (PROJECT_ROOT, SRC_PATH):
    pstr = str(_pth)
    if pstr not in sys.path:
        sys.path.append(pstr)

# Set up logging before other imports
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Now import our modules
from pdfminer.high_level import extract_text as _pdfminer_extract

from services.paper.embedding import embed_query
from services.paper.text_extractor import _clean_text
from services.paper.vector_store import build_index_from_text, index_exists, index_path, search

try:
    import fitz  # PyMuPDF

    def _extract_pdf_text(pdf_path: Path) -> str:
        with fitz.open(pdf_path) as doc:
            return "\n".join(page.get_text("text") for page in doc)

except ModuleNotFoundError:  # fallback to pdfminer

    def _extract_pdf_text(pdf_path: Path) -> str:
        return _pdfminer_extract(str(pdf_path))


def index_pdf(pdf_path: Path, paper_id: str) -> None:
    """Index a PDF file with the given paper ID."""
    if index_exists(paper_id):
        logger.warning(
            f"Index for {paper_id} already exists. Delete it first if you want to re-index."
        )
        return

    if not pdf_path.exists():
        logger.error(f"PDF file not found: {pdf_path}")
        return

    logger.info(f"Extracting text from {pdf_path}...")
    try:
        text = _extract_pdf_text(pdf_path)
        text = _clean_text(text)
        if not text or len(text) < 100:  # Sanity check
            logger.error("Extracted text seems too short. Extraction may have failed.")
            return

        logger.info(f"Building vector index for {paper_id}...")
        build_index_from_text(paper_id, text)
        logger.info(f"Successfully indexed {paper_id} with {len(text):,} characters")
    except Exception as e:
        logger.error(f"Failed to index {pdf_path}: {e}", exc_info=True)


def search_index(paper_id: str, query: str, top_k: int = 3) -> None:
    """Search the vector index for the given paper ID and query."""
    if not index_exists(paper_id):
        logger.error(f"No index found for {paper_id}. Index a PDF first.")
        return

    logger.info(f"Searching {paper_id} for: {query}")
    try:
        query_embedding = embed_query(query)
        results = search(paper_id, query_embedding, top_k=top_k)

        if not results:
            logger.info("No results found.")
            return

        logger.info(f"\n{'=' * 80}\nTop {len(results)} results:\n{'=' * 80}")
        for i, result in enumerate(results, 1):
            full = result["text"].replace("\n", " ").strip()
            score = result.get("_distance", 0.0)
            is_trunc = len(full) > 500
            preview = full[:500] + (" …" if is_trunc else "")
            print(f"\n[Result {i}, Score: {score:.3f}]\n{preview}\n{'─' * 80}")
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)


def list_indices() -> None:
    """List all available vector indices."""
    from config import config

    if not config.VECTOR_CACHE_DIR.exists():
        logger.info("No vector cache directory found.")
        return

    indices = [p.stem for p in config.VECTOR_CACHE_DIR.glob("*.lance")]
    if not indices:
        logger.info("No vector indices found.")
        return

    logger.info("Available vector indices:")
    for idx in sorted(indices):
        print(f"- {idx}")


def delete_index_cli(paper_id: str) -> None:
    """Delete a vector index."""
    if not index_exists(paper_id):
        logger.error(f"No index found for {paper_id}")
        return

    try:
        import shutil

        path = index_path(paper_id)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            logger.info("Deleted index at %s", path)
        else:
            logger.warning("Index path %s not found", path)
    except Exception as e:
        logger.error("Failed to delete index: %s", e)


def _download_pdf(url: str) -> Path:
    """Download a PDF to a temporary file and return its path."""

    logger.info(f"Downloading PDF from {url} …")
    response = requests.get(url, timeout=30)
    response.raise_for_status()

    if "application/pdf" not in response.headers.get("content-type", ""):
        logger.warning(
            "URL does not appear to be a PDF (content-type %s)",
            response.headers.get("content-type"),
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(response.content)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def _derive_paper_id(source: str) -> str:
    """Derive a paper_id from a file path or URL if not provided by user."""

    name = Path(urlparse(source).path if source.startswith("http") else source).stem
    # Simple slug: strip spaces, special chars
    return "".join(c for c in name if c.isalnum() or c in ("-", "_"))[:64]


def chat_loop(paper_id: str, top_k: int = 4) -> None:
    """Simple interactive retrieval loop."""

    if not index_exists(paper_id):
        logger.error("No index found for %s", paper_id)
        return

    print("\nEnter your question (type 'exit' to quit):")
    while True:
        try:
            query = input(">> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if query.lower() in {"exit", "quit", "q"}:
            break

        if not query:
            continue

        query_embedding = embed_query(query)
        results = search(paper_id, query_embedding, top_k=top_k)

        if not results:
            print("No matching chunks found.\n")
            continue

        print(f"\nTop {len(results)} chunks:\n{'=' * 80}")
        for i, res in enumerate(results, 1):
            full = res["text"].replace("\n", " ").strip()
            score = res.get("_distance", 0.0)
            is_trunc = len(full) > 350
            snippet = full[:350] + (" …" if is_trunc else "")
            print(f"[{i} | {score:.3f}] {snippet}\n{'─' * 80}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test RAG with LanceDB vector store")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Index command
    index_parser = subparsers.add_parser("index", help="Index a PDF file")
    index_parser.add_argument("pdf_path", type=Path, help="Path to PDF file")
    index_parser.add_argument("--paper-id", required=True, help="Unique ID for this paper")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search an indexed paper")
    search_parser.add_argument("paper_id", help="Paper ID to search")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--top-k", type=int, default=3, help="Number of results to return")

    # List command
    subparsers.add_parser("list", help="List all vector indices")

    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a vector index")
    delete_parser.add_argument("paper_id", help="Paper ID to delete")

    # Index a PDF from URL
    idx_url_parser = subparsers.add_parser("index-url", help="Download and index a PDF from URL")
    idx_url_parser.add_argument("pdf_url", help="URL to a PDF file")
    idx_url_parser.add_argument("--paper-id", help="Optional paper id (defaults to filename)")

    # Interactive chat command
    chat_parser = subparsers.add_parser("chat", help="Interactive retrieval chat")
    chat_parser.add_argument("paper_id", help="Paper ID to chat with (must be indexed)")
    chat_parser.add_argument("--top-k", type=int, default=4, help="Chunks per response")

    # Chat directly from URL (download, index if needed, then open chat)
    chat_url_parser = subparsers.add_parser(
        "chat-url", help="Download a PDF and chat with it interactively"
    )
    chat_url_parser.add_argument("pdf_url", help="URL to a PDF")
    chat_url_parser.add_argument("--paper-id", help="Optional paper id")
    chat_url_parser.add_argument("--top-k", type=int, default=4, help="Chunks per response")

    args = parser.parse_args()

    if args.command == "index":
        index_pdf(args.pdf_path, args.paper_id)
    elif args.command == "search":
        search_index(args.paper_id, args.query, args.top_k)
    elif args.command == "list":
        list_indices()
    elif args.command == "delete":
        delete_index_cli(args.paper_id)
    elif args.command == "index-url":
        pid = args.paper_id or _derive_paper_id(args.pdf_url)
        pdf_path = _download_pdf(args.pdf_url)
        index_pdf(pdf_path, pid)
    elif args.command == "chat":
        chat_loop(args.paper_id, args.top_k)
    elif args.command == "chat-url":
        pid = args.paper_id or _derive_paper_id(args.pdf_url)
        if not index_exists(pid):
            pdf_path = _download_pdf(args.pdf_url)
            index_pdf(pdf_path, pid)
        chat_loop(pid, args.top_k)


if __name__ == "__main__":
    main()
