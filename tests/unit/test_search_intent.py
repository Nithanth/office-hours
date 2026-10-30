"""Intent detection for the one-box search input."""

from api.routes.search import _detect_author_query, _intent_banner


def test_detects_author_phrases():
    assert _detect_author_query("papers by Yann LeCun") == (
        "Yann LeCun",
        True,
        None,
    )
    assert _detect_author_query("Papers by Vaswani?") == ("Vaswani", True, None)
    assert _detect_author_query("works from Geoffrey Hinton") == (
        "Geoffrey Hinton",
        True,
        None,
    )
    assert _detect_author_query("publications of Fei-Fei Li") == (
        "Fei-Fei Li",
        True,
        None,
    )
    assert _detect_author_query("research by Jiaya Jia") == (
        "Jiaya Jia",
        True,
        None,
    )


def test_detects_author_keyword_triangulation():
    assert _detect_author_query("papers by Vincent Sitzmann about siren") == (
        "Vincent Sitzmann",
        True,
        "siren",
    )
    assert _detect_author_query("works by yann lecun on self-supervised learning") == (
        "yann lecun",
        True,
        "self-supervised learning",
    )
    assert _detect_author_query("papers by Vaswani for attention") == (
        "Vaswani",
        True,
        "attention",
    )


def test_detects_bare_names():
    assert _detect_author_query("Vincent Sitzmann") == (
        "Vincent Sitzmann",
        False,
        None,
    )
    assert _detect_author_query("Yann LeCun") == ("Yann LeCun", False, None)
    assert _detect_author_query("Laurens van der Maaten") == (
        "Laurens van der Maaten",
        False,
        None,
    )


def test_no_author_intent():
    assert _detect_author_query("attention is all you need") is None
    assert _detect_author_query("papers") is None
    assert _detect_author_query("papers, by the way") is None
    assert _detect_author_query("https://arxiv.org/abs/1706.03762") is None
    assert _detect_author_query("") is None


def test_banner_offers_topic_correction():
    b = _intent_banner("Author search", "papers by X")
    assert "Author search" in b
    assert "hx-post='/search'" in b
    assert "hx-vals" in b
    assert "topic" in b


def test_banner_keeps_hxvals_json_safe():
    # Quotes/angle brackets in the query must survive as JSON inside the
    # single-quoted hx-vals attribute (json.dumps then HTML-escape).
    b = _intent_banner("detected", 'papers by "weird" <name>')
    assert "<name>" not in b
    assert "&lt;name&gt;" in b
    assert "&#x27;" not in b.split("hx-vals")[0]  # attribute itself stays intact
