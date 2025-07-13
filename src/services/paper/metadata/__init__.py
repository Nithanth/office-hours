"""Lightweight paper-metadata helpers (arXiv, DOI, Crossref).

The public surface area is limited to the ``fetch_*`` functions re-exported
below so other services can do::

    from services.paper.metadata import fetch_arxiv_metadata

No heavy imports are triggered at module import-time.
"""

from .metadata_fetchers import (
    fetch_arxiv_metadata as fetch_arxiv_metadata,
)
from .metadata_fetchers import (
    fetch_crossref_metadata as fetch_crossref_metadata,
)
from .metadata_fetchers import (
    fetch_doi_metadata as fetch_doi_metadata,
)
from .metadata_fetchers import (
    fetch_semantic_scholar_arxiv as fetch_semantic_scholar_arxiv,
)

# Public re-export list
__all__: list[str] = [
    "fetch_arxiv_metadata",
    "fetch_crossref_metadata",
    "fetch_doi_metadata",
    "fetch_semantic_scholar_arxiv",
]
