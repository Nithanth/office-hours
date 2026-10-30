"""Server-side keyword filter for author-topic triangulation."""

from api.routes.author import _paper_matches_keyword
from services.paper.metadata.author_search import AuthorPaper


def _paper(**kw) -> AuthorPaper:
    return AuthorPaper(paper_id="p", title=kw.pop("title", ""), **kw)


def test_matches_title():
    p = _paper(title="SIREN: Implicit Neural Representations")
    assert _paper_matches_keyword(p, "siren")


def test_matches_abstract_and_venue():
    p = _paper(title="Neural Systems", abstract="we adopt siren layers")
    assert _paper_matches_keyword(p, "siren")
    v = _paper(title="Neural Systems", venue="SIREN Workshop")
    assert _paper_matches_keyword(v, "siren")


def test_substring_does_not_match():
    p = _paper(title="Training Language Models")
    assert not _paper_matches_keyword(p, "ai")


def test_punctuation_ending_tokens_match():
    p = _paper(title="C++ Techniques for Scientific Computing")
    assert _paper_matches_keyword(p, "c++")
    q = _paper(title="F# and C# in production")
    assert _paper_matches_keyword(q, "c#")


def test_all_tokens_required_across_fields():
    p = _paper(
        title="Neural Systems",
        abstract="implicit representations",
        venue="ICLR",
    )
    assert _paper_matches_keyword(p, "neural iclr")
    assert not _paper_matches_keyword(p, "neural missing-term")
