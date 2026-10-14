"""Topics v1: canonicalization, kind detection, repo helpers."""

from __future__ import annotations

from services.topics.keys import (
    canonical_key_for,
    detect_topic_kind,
    normalize_topic_key,
)


class TestNormalizeTopicKey:
    def test_strips_filler_prefixes(self):
        assert normalize_topic_key("papers on implicit neural representations") == (
            "implicit neural representations"
        )
        assert normalize_topic_key("Research about GNNs") == "gnns"

    def test_punctuation_and_case_collapse(self):
        a = normalize_topic_key("Implicit Neural Representations")
        b = normalize_topic_key("implicit neural representations")
        assert a == b == "implicit neural representations"

    def test_phrase_variants_share_key(self):
        assert canonical_key_for(
            "papers about diffusion models", kind="subject"
        ) == canonical_key_for("diffusion models", kind="subject")


class TestDetectTopicKind:
    def test_url_is_paper(self):
        assert detect_topic_kind("https://arxiv.org/abs/2106.05234") == "paper"
        assert detect_topic_kind("2106.05234") == "paper"
        assert detect_topic_kind("anything", is_url_input=True) == "paper"

    def test_author_phrase_is_author(self):
        assert detect_topic_kind("papers by Yann LeCun") == "author"

    def test_plain_query_is_subject(self):
        assert detect_topic_kind("neural radiance fields") == "subject"

    def test_author_key_is_normalized_name(self):
        assert canonical_key_for("papers by Yann LeCun", kind="author") == "yann lecun"
