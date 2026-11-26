import pytest
from fastapi import HTTPException

from services.lessons import library_context
from services.lessons.library_context import LibraryContext, Paper, library_evidence


@pytest.mark.asyncio
async def test_two_papers_with_the_same_page_number_keep_distinct_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def evidence(url: str, pdf: bytes, first: int) -> tuple[list[dict], list[str]]:
        return [
            {"type": "text", "text": "PDF page 1\nPaper text"},
            {"type": "image_url", "image_url": {"url": url}},
        ], ["PDF page 1"]

    monkeypatch.setattr(library_context, "evidence", evidence)
    sources = LibraryContext(
        "A topic", [Paper("One", "url1"), Paper("Two", "url2")], "A topic report."
    )
    parts, refs, images = await library_evidence(sources, 1)
    assert refs == ["Topic report", "1. One · PDF page 1", "2. Two · PDF page 1"]
    assert images == {refs[1]: "url1", refs[2]: "url2"}
    assert any(p.get("text") == refs[2] + "\nPaper text" for p in parts)


@pytest.mark.asyncio
async def test_unreadable_paper_never_becomes_an_unrestricted_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(url: str, pdf: bytes, first: int) -> None:
        raise HTTPException(422)

    monkeypatch.setattr(library_context, "evidence", unavailable)
    with pytest.raises(HTTPException, match="No readable source"):
        await library_evidence(LibraryContext("Paper", [Paper("One", "url1")]), 1)
