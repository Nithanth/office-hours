"""Helpers for building LLM prompts + token targets for paper and topic summaries."""

from __future__ import annotations

import html
from datetime import datetime

from loguru import logger

from models.paper import Paper
from prompts.prompt_builder import get_topic_analysis_prompts
from prompts.prompt_utils import (
    get_output_token_target,
    max_content_snippet_chars,
)

__all__ = [
    "PaperSummariser",
    "TopicSummariser",
]


class _BaseSummariser:
    """Shared summarisation helper base class for paper/topic summaries."""

    def __init__(self, *, selected_model_input_token_limit: int) -> None:
        self._input_tok_limit = selected_model_input_token_limit

    # ------------------------------------------------------------------
    # Internal shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_date(date_in: str | datetime | None) -> str:
        if not date_in:
            return "N/A"
        if isinstance(date_in, datetime):
            return date_in.strftime("%Y-%m-%d")
        try:
            return datetime.strptime(date_in, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return str(date_in)


class PaperSummariser(_BaseSummariser):
    """Create per-paper summary prompt + output token budget."""

    def build_prompt_and_target(
        self,
        paper: Paper,
        *,
        audience_type: str,
        summary_size_key: str,
        length_guidance: str,
        prompt_template: str,
        snippet_char_limit: int | None = None,
    ) -> tuple[str, int]:
        if snippet_char_limit is None:
            snippet_char_limit = max_content_snippet_chars(
                selected_model_input_token_limit=self._input_tok_limit,
                audience_type=audience_type,
                summary_size_key=summary_size_key,
                is_topic_summary=False,
            )

        snippet = (paper.text_content or "")[:snippet_char_limit]
        fallback_note = (
            "NOTE: Full text was unavailable; summary must rely on limited context "
            "(abstract/metadata).\n\n"
            if len(snippet.strip()) < 200
            else ""
        )

        prompt = fallback_note + prompt_template.format(
            paper_title=html.escape(paper.title or "N/A"),
            paper_authors=html.escape(", ".join(paper.authors) if paper.authors else "N/A"),
            paper_publication_date=html.escape(self._format_date(paper.published_date)),
            paper_content_snippet=snippet,
            overall_instructions=length_guidance,
            target_audience=audience_type,
        )

        tok_target = get_output_token_target(
            audience_type,
            summary_size_key,
            is_topic_summary=False,
        )
        logger.debug(
            "paper_prompt_built", paper_id=paper.id, tgt_tok=tok_target, prompt_len=len(prompt)
        )
        return prompt, tok_target


class TopicSummariser(_BaseSummariser):
    """Create system/user prompts + token target for topic overviews."""

    def build_prompts_and_target(
        self,
        *,
        original_query: str,
        papers: list[Paper],
        audience_type: str,
        summary_size_key: str,
        length_guidance: str,
        snippet_char_limit: int | None = None,
    ) -> tuple[tuple[str, str], int]:
        assert papers, "papers list cannot be empty for topic summarisation"

        if snippet_char_limit is None:
            snippet_char_limit = max_content_snippet_chars(
                selected_model_input_token_limit=self._input_tok_limit,
                audience_type=audience_type,
                summary_size_key=summary_size_key,
                is_topic_summary=True,
            )

        consolidated_parts: list[str] = []
        for p in papers:
            title = html.escape(p.title or "N/A")
            authors = html.escape(", ".join(a for a in p.authors if a) if p.authors else "N/A")
            pub_date = html.escape(self._format_date(p.published_date))
            snippet = html.escape((p.text_content or "")[:snippet_char_limit])
            consolidated_parts.append(
                f"Title: {title}\nAuthors: {authors}\nPublication Date: {pub_date}\nContent Snippet: {snippet}\n\n---\n\n"
            )

        system_prompt, user_prompt = get_topic_analysis_prompts(
            topic=original_query,
            num_papers_to_synthesize=len(papers),
            target_audience=audience_type,
            context_snippets="".join(consolidated_parts),
            overall_instructions=length_guidance,
            summary_size_key=summary_size_key,
        )

        tok_target = get_output_token_target(
            audience_type,
            summary_size_key,
            is_topic_summary=True,
        )
        logger.debug(
            "topic_prompt_built",
            num_papers=len(papers),
            tgt_tok=tok_target,
            user_prompt_len=len(user_prompt),
        )
        return (system_prompt, user_prompt), tok_target
