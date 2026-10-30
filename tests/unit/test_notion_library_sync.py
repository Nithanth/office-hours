"""Unit tests for the Notion library-sync pure functions."""

from __future__ import annotations

import pytest

from services.notion.library_sync import (
    _paper_properties,
    _topic_properties,
    unanswered_threads,
)


def _paper(**over):
    base = {
        "paper_id": "https://arxiv.org/abs/1706.03762",
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/abs/1706.03762",
        "authors": {"list": ["Vaswani", "Shazeer"]},
        "status": "reading",
        "report_id": "r1",
        "notion_page_id": None,
        "saved_date": "2026-10-13",
    }
    base.update(over)
    return base


def _report(**over):
    base = {
        "title": "Attention mechanisms",
        "original_query": "transformer attention",
        "created_date": "2026-10-12",
        "notion_page_id": None,
    }
    base.update(over)
    return base


class TestPaperProperties:
    def test_basic_row(self):
        props = _paper_properties(_paper(), None)
        assert props["Name"]["title"][0]["text"]["content"] == (
            "Attention Is All You Need"
        )
        assert props["Status"]["select"]["name"] == "Reading"
        assert props["Saved"]["date"]["start"] == "2026-10-13"
        assert props["Source URL"]["url"] == "https://arxiv.org/abs/1706.03762"
        assert "Vaswani" in props["Authors"]["rich_text"][0]["text"]["content"]
        assert "Topic" not in props  # no relation without a topic page

    def test_topic_relation_when_page_known(self):
        props = _paper_properties(_paper(), "topic-page-123")
        assert props["Topic"]["relation"] == [{"id": "topic-page-123"}]

    def test_no_url_omits_source(self):
        props = _paper_properties(_paper(url=None), None)
        assert "Source URL" not in props


class TestTopicProperties:
    def test_basic_row(self):
        props = _topic_properties(_report(), "r1")
        assert props["Name"]["title"][0]["text"]["content"] == ("Attention mechanisms")
        assert props["Created"]["date"]["start"] == "2026-10-12"
        assert "transformer" in props["Query"]["rich_text"][0]["text"]["content"]
        oh_id = props["Office Hours ID"]["rich_text"][0]["text"]["content"]
        assert oh_id == "report:r1"

    def test_empty_query_omitted(self):
        props = _topic_properties(_report(original_query=""), "r1")
        assert "Query" not in props


class TestUnansweredThreads:
    def _comment(self, did, author, text, ts="2026-10-13T10:00:00Z"):
        return {
            "id": f"c-{ts}",
            "discussion_id": did,
            "created_by": {"id": author},
            "created_time": ts,
            "rich_text": [{"plain_text": text}],
        }

    def test_human_question_unanswered(self):
        comments = [self._comment("d1", "user-1", "what is the limitation?")]
        out = unanswered_threads(comments, bot_id="bot-9")
        assert out == [("d1", "what is the limitation?")]

    def test_bot_replied_thread_skipped(self):
        comments = [
            self._comment("d1", "user-1", "question?", "2026-10-13T09:00:00Z"),
            self._comment("d1", "bot-9", "answer.", "2026-10-13T10:00:00Z"),
        ]
        assert unanswered_threads(comments, bot_id="bot-9") == []

    def test_new_human_comment_after_bot_reply_counts(self):
        comments = [
            self._comment("d1", "user-1", "q1?", "2026-10-13T08:00:00Z"),
            self._comment("d1", "bot-9", "a1.", "2026-10-13T09:00:00Z"),
            self._comment("d1", "user-1", "followup?", "2026-10-13T10:00:00Z"),
        ]
        out = unanswered_threads(comments, bot_id="bot-9")
        assert len(out) == 1
        assert out[0][0] == "d1"

    def test_multiple_discussions(self):
        comments = [
            self._comment("d1", "user-1", "q1?", "2026-10-13T08:00:00Z"),
            self._comment("d2", "user-2", "q2?", "2026-10-13T09:00:00Z"),
            self._comment("d2", "bot-9", "a2.", "2026-10-13T10:00:00Z"),
        ]
        out = unanswered_threads(comments, bot_id="bot-9")
        assert [d for d, _ in out] == ["d1"]

    def test_empty(self):
        assert unanswered_threads([], bot_id="bot-9") == []


class TestAnswerPageComments:
    """The shared sweep helper: unanswered threads get a grounded,
    attributed reply; bot threads and answer failures are handled."""

    @staticmethod
    def _comment(discussion_id, created_by, text, created_time="2026-10-13T09:00:00Z"):
        return {
            "discussion_id": discussion_id,
            "created_time": created_time,
            "created_by": {"id": created_by},
            "rich_text": [{"plain_text": text}],
        }

    class _FakeExporter:
        def __init__(self, comments):
            self._comments = comments
            self.replies = []

        async def list_comments(self, page_id):
            return self._comments

        async def reply_to_discussion(self, discussion_id, text):
            self.replies.append((discussion_id, text))

    @pytest.mark.asyncio
    async def test_answers_and_attributes(self):
        from services.notion.library_sync import _answer_page_comments

        exporter = self._FakeExporter(
            [self._comment("d1", "user-1", "which papers argue against?")]
        )

        async def fake_answer(question):
            return "T2A-GAN [3] argues attention transfer is lossy."

        answered = await _answer_page_comments(
            exporter,
            notion_page_id="page-1",
            answer_fn=fake_answer,
            bot_id="bot-9",
        )
        assert answered == 1
        disc_id, text = exporter.replies[0]
        assert disc_id == "d1"
        assert text.startswith("**Office Hours:**")
        # [n] chunk citations strip out — no Sources footer in Notion.
        assert "[3]" not in text

    @pytest.mark.asyncio
    async def test_answer_failure_skips_thread(self):
        from services.notion.library_sync import _answer_page_comments

        exporter = self._FakeExporter(
            [
                self._comment("d1", "user-1", "boom?"),
                self._comment("d2", "user-1", "ok?", "2026-10-13T10:00:00Z"),
            ]
        )
        calls = 0

        async def flaky(question):
            nonlocal calls
            calls += 1
            if question == "boom?":
                raise RuntimeError("llm down")
            return "fine."

        answered = await _answer_page_comments(
            exporter,
            notion_page_id="page-1",
            answer_fn=flaky,
            bot_id="bot-9",
        )
        # One thread failed, the other still answered.
        assert answered == 1
        assert exporter.replies[0][0] == "d2"

    @pytest.mark.asyncio
    async def test_topic_comments_route_to_topic_answerer(self, monkeypatch):
        from services.notion import library_sync

        exporter = self._FakeExporter(
            [self._comment("d1", "user-1", "summarize the thread?")]
        )
        seen = {}

        async def fake_topic_answer(*, topic_id, question, user_id):
            seen["topic_id"] = topic_id
            seen["user_id"] = user_id
            return "topic-grounded."

        monkeypatch.setattr(
            library_sync, "answer_question_for_topic", fake_topic_answer
        )
        answered = await library_sync.answer_comments_for_topic(
            exporter,
            notion_page_id="topic-page-1",
            topic_id="topic-42",
            user_id="u-1",
            bot_id="bot-9",
        )
        assert answered == 1
        assert seen == {"topic_id": "topic-42", "user_id": "u-1"}


class TestMarkdownToRichText:
    def test_bold_and_equation(self):
        from services.notion.markdown_to_blocks import markdown_to_rich_text

        segs = markdown_to_rich_text(
            "The limit is **quadratic** complexity $O(n^2 \\cdot d)$ per layer."
        )
        assert any(s["type"] == "equation" for s in segs)
        eq = next(s for s in segs if s["type"] == "equation")
        assert eq["equation"]["expression"] == "O(n^2 \\cdot d)"
        bold = next(s for s in segs if s["type"] == "text" and s["annotations"]["bold"])
        assert bold["text"]["content"] == "quadratic"

    def test_paren_math(self):
        from services.notion.markdown_to_blocks import markdown_to_rich_text

        segs = markdown_to_rich_text("growth is \\(O(n^2)\\) fast")
        eq = next(s for s in segs if s["type"] == "equation")
        assert eq["equation"]["expression"] == "O(n^2)"

    def test_plain_text(self):
        from services.notion.markdown_to_blocks import markdown_to_rich_text

        segs = markdown_to_rich_text("no formatting here")
        assert len(segs) == 1
        assert segs[0]["type"] == "text"


class TestChatBlocks:
    def test_user_and_assistant_blocks(self):
        from services.notion.library_sync import _chat_blocks

        blocks = _chat_blocks(
            [
                {"role": "user", "content": "what is attention?"},
                {"role": "assistant", "content": "A weighting over values [3]."},
            ],
            heading=True,
        )
        assert blocks[0]["type"] == "heading_3"
        rich = blocks[0]["heading_3"]["rich_text"][0]["text"]["content"]
        assert rich == "Q&A"
        text = " ".join(
            rt.get("text", {}).get("content", "")
            for b in blocks
            for rt in b.get(b["type"], {}).get("rich_text", [])
        )
        assert "**You:**" not in text  # rendered as annotation, not literal
        assert "You:" in text
        assert "Office Hours:" in text
        assert "[3]" not in text  # citations stripped

    def test_no_heading_for_incremental(self):
        from services.notion.library_sync import _chat_blocks

        blocks = _chat_blocks([{"role": "user", "content": "q?"}], heading=False)
        assert all(b["type"] != "heading_3" for b in blocks)


class TestPaperIntro:
    def test_tldr_included(self):
        from services.notion.library_sync import _paper_intro_markdown

        md = _paper_intro_markdown(_paper(), "Great paper about attention.")
        assert "Great paper about attention." in md
        assert "arxiv.org" in md
        assert "## About this paper" in md

    def test_fallback_intro(self):
        from services.notion.library_sync import _paper_intro_markdown

        md = _paper_intro_markdown(_paper(url=None), None)
        assert "Comment a question" in md
        assert "**Source:**" not in md
