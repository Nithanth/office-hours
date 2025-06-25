"""Unit tests for TextExtractor logic using mocked layers.
Run with: pytest -q tests/test_text_extractor_unit.py
"""

from pathlib import Path

import pytest

from services.text_extractor import TextExtractor


@pytest.fixture(name="extractor")
def _extractor_instance() -> TextExtractor:
    """Return a *fresh* singleton instance for each test."""
    # Reset singleton for clean slate
    TextExtractor._instance = None
    return TextExtractor.instance()


@pytest.mark.asyncio
async def test_ensure_cached_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extractor: TextExtractor
) -> None:
    """ensure_cached should write a file and return its path when any layer succeeds."""
    monkeypatch.setattr("services.text_extractor.CACHE_ROOT", tmp_path)

    success_text = "A" * 9000  # Passes _good_quality check (>8k chars)

    async def _fake_layer(_: str) -> str:
        return success_text

    # Monkeypatch the first layer to succeed and others to raise if called
    monkeypatch.setattr(extractor, "_layer_exa_contents", _fake_layer)
    monkeypatch.setattr(
        extractor, "_layer_arxiv", lambda _url: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(extractor, "_layer_pdf", lambda _url: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(
        extractor, "_layer_html", lambda _url: (_ for _ in ()).throw(RuntimeError())
    )

    url = "https://example.com/paper123"
    path_str = await extractor.ensure_cached(url, "paper123", force=True)
    assert path_str, "ensure_cached should return a cache path"

    cache_path = Path(path_str)
    assert cache_path.exists(), "Cache file should be written"
    assert cache_path.read_text(encoding="utf-8") == success_text

    # Subsequent call should hit cache and *not* call extractor again
    called = False

    async def _spy_layer(_: str) -> str:
        nonlocal called
        called = True
        return success_text

    monkeypatch.setattr(extractor, "_layer_exa_contents", _spy_layer)
    await extractor.ensure_cached(url, "paper123")
    assert not called, "Extractor layers should not run when cache exists"


@pytest.mark.asyncio
async def test_load_full_text_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extractor: TextExtractor
) -> None:
    """load_full_text should return the cache path once file exists."""
    monkeypatch.setattr("services.text_extractor.CACHE_ROOT", tmp_path)
    # prepare dummy cache
    paper_id = "dummy123"
    dummy_file = tmp_path / f"{paper_id}_dummy.txt"
    text = "hello world"
    dummy_file.write_text(text, encoding="utf-8")

    path = await extractor.load_full_text(paper_id)
    assert path == str(dummy_file)

    # read_full_text helper
    read_text = await extractor.read_full_text(paper_id)
    assert read_text == text
