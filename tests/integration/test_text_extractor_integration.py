"""Simple integration test hitting Exa get_contents (network).
Requires EXA_API_KEY env var. Skip if missing.
"""

import os
from pathlib import Path
import pytest

from services.text_extractor import TextExtractor, CACHE_ROOT

EXAMPLE_PDF_URL = "https://arxiv.org/pdf/2103.00020.pdf"  # CLIP paper (~1.7MB)


@pytest.mark.asyncio
@pytest.mark.network
async def test_extract_arxiv_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # noqa: D401
    # Isolate the cache to a temporary directory for this test
    monkeypatch.setattr("services.text_extractor.CACHE_ROOT", tmp_path)

    if not os.getenv("EXA_API_KEY"):
        pytest.skip("EXA_API_KEY not set – skipping network integration test")

    extractor = TextExtractor.instance()
    # Force prefer_exa=False so we hit PDF layer regardless of Exa account limits
    path = await extractor.ensure_cached(EXAMPLE_PDF_URL, "clip_test", prefer_exa=False, force=True)
    assert path and os.path.exists(path)

    # Read the text we just cached to avoid picking up an old cache file
    text = Path(path).read_text(encoding="utf-8")
    # Should get a decent chunk of text with the CLIP paper title present
    assert text and "Learning Transferable Visual Models" in text
    # Ensure length looks reasonable (> 20k chars for CLIP PDF)
    assert len(text) > 20000
