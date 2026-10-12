from pathlib import Path

import pytest

from services.paper.text_extractor import _MIN_FULL_TEXT, TextExtractor


@pytest.mark.network  # Requires outbound HTTP to arXiv
@pytest.mark.asyncio
async def test_arxiv_abs_url_yields_full_pdf(tmp_path: Path, monkeypatch):
    """Ensure an arXiv *abs* link is upgraded to PDF, cached once, and full text is extracted."""

    # Use a known small PDF with reliable accessibility.
    url = "https://arxiv.org/abs/2003.08934"

    # Force cache into tmpdir to avoid polluting real cache during CI
    monkeypatch.setattr("services.paper.text_extractor.CACHE_ROOT", tmp_path)

    extractor = TextExtractor.instance()

    # Use a safe paper_id – tests' stubbed _cache_path uses this directly in the
    # filename, so it must not contain path separators.
    safe_pid = "arxiv_2003_08934"

    # Force fresh extraction
    path = await extractor.ensure_cached(
        url, paper_id=safe_pid, force=True, prefer_exa=False
    )
    assert path is not None, "Extraction layer returned None"

    # Should *not* be marked partial
    assert not path.endswith(
        ".partial.txt"
    ), "Fell back to partial abstract instead of full PDF"

    text = Path(path).read_text(encoding="utf-8")

    # Full length threshold reached
    assert (
        len(text) >= _MIN_FULL_TEXT
    ), f"Extracted text too short: {len(text)} < {_MIN_FULL_TEXT}"
