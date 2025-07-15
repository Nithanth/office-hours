import os

import pytest

from services.core.exa_client import ExaClient

ARXIV_URL = "https://arxiv.org/abs/2006.09661"


@pytest.mark.skipif(
    os.getenv("EXA_API_KEY") is None,
    reason="Set EXA_API_KEY to run metadata enrichment integration test",
)
def test_enrich_url_populates_metadata() -> None:
    """ExaClient.enrich_url should fill critical metadata fields."""
    client = ExaClient(api_key=os.environ["EXA_API_KEY"])
    paper = client.enrich_url(ARXIV_URL)

    assert paper is not None, "enrich_url returned None"
    assert paper.authors, "Authors list is empty"
    assert paper.published_date is not None, "Published date missing"
