"""infra.metadata package
Lazy exports for lightweight helpers used by :pymod:`infra.exa_client`.
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
