import os
import sys
from pathlib import Path
from pprint import pprint
from typing import Optional

# Ensure project src/ directory is on PYTHONPATH when script is run directly
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

# Allow `python scripts/test_scratch.py <paper_url>`
PAPER_URL: str = sys.argv[1] if len(sys.argv) > 1 else "https://arxiv.org/pdf/2103.03123"

from infra.exa_client import ExaClient  # noqa: E402 – after path tweak


def main(url: str) -> None:
    exa_api_key = os.getenv("EXA_API_KEY")
    if not exa_api_key:
        print("ERROR: Set EXA_API_KEY in your environment.")
        sys.exit(1)

    client = ExaClient(api_key=exa_api_key)

    client.clear_cache()

    print("=== Testing metadata pipelines for ===\n", url, "\n")

    # -------------------- arXiv --------------------
    arxiv_id: Optional[str] = client._extract_arxiv_id(url)
    if arxiv_id:
        print(f"[arXiv] Fetching metadata for ID {arxiv_id} …")
        arxiv_meta = client._fetch_arxiv_metadata(arxiv_id)
        pprint(arxiv_meta, compact=True)
    else:
        print("[arXiv] No arXiv ID detected from URL")

    # -------------------- DOI-based APIs --------------------
    doi_val: Optional[str] = client._extract_doi(url)
    if doi_val:
        print(f"\n[Semantic Scholar] Metadata for DOI {doi_val} …")
        ss_meta = client._fetch_doi_metadata(doi_val)
        pprint(ss_meta, compact=True)

        print(f"\n[Crossref] Metadata for DOI {doi_val} …")
        cr_meta = client._fetch_crossref_metadata(doi_val)
        pprint(cr_meta, compact=True)
    else:
        print("\n[DOI] No DOI detected in URL – skipping Semantic Scholar / Crossref fetches")

    # -------------------- Exa get_contents --------------------
    print("\n[Exa] search_and_contents abs page …")
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    res = client.adapter.search_and_contents(f'url:"{abs_url}"', num_results=1, text=True)
    first = res.results[0]
    if hasattr(first, "dict"):
        pprint(first.dict(), depth=2)
    else:
        # Fallback: build a simple dict of common attrs
        preview = {
            "title": getattr(first, "title", None),
            "authors": getattr(first, "authors", None) or getattr(first, "author", None),
            "published_date": getattr(first, "published_date", None),
            "url": getattr(first, "url", None),
            "snippet": getattr(first, "snippet", "")[:200],
        }
        pprint(preview, compact=True)


if __name__ == "__main__":
    main(PAPER_URL)
