"""Report generation service.

Generates per-paper and topic summaries by orchestrating LLM calls and
Exa searches.  This is the canonical implementation; legacy shim paths
have been removed."""

import asyncio
import hashlib
import html
import logging
import re
import time
import urllib.parse
from datetime import datetime
from functools import partial
from typing import Any, Optional, Union, cast

from config import config
from infra.exa_client import ExaClient
from infra.llm_service import LLMProviderName, LLMService
from models.paper import Paper
from prompts.prompt_templates import (
    AUDIENCE_PROMPT_MAP,
    DEFAULT_SYSTEM_PROMPT,  # local import to avoid cyclical issues
    get_topic_analysis_prompts,
)
from utils.summary_cache import SummaryCache

logger = logging.getLogger(__name__)


class ReportGenerator:
    # Average characters per token, can be adjusted based on observations
    AVG_CHARS_PER_TOKEN: int = 4
    # Estimated token overhead for the static parts of the prompt template (instructions, boilerplate, etc.)
    PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS: int = 350
    # Additional safety buffer in token calculations
    SAFETY_MARGIN_TOKENS: int = 150  # Increased slightly

    QUALITATIVE_LENGTH_GUIDANCE: dict[str, str] = config.QUALITATIVE_LENGTH_GUIDANCE
    SUMMARY_OUTPUT_TOKEN_TARGETS: dict[str, dict[str, int]] = config.SUMMARY_OUTPUT_TOKEN_TARGETS

    def __init__(
        self,
        llm_service: LLMService,
        exa_client: ExaClient,
        selected_model_api_id: str,
        selected_model_provider: LLMProviderName,
        selected_model_input_token_limit: int,
    ):
        self.llm_service = llm_service
        self.exa_client = exa_client
        self.selected_model_api_id = selected_model_api_id
        self.selected_model_provider = selected_model_provider
        self.selected_model_input_token_limit = selected_model_input_token_limit

    def _get_output_token_target(
        self, audience_type: str, summary_size_key: str, is_topic_summary: bool
    ) -> int:
        """Helper to get the target output token count for a summary."""
        if is_topic_summary:
            # Topic summaries might have different sizing logic or use a different part of the config
            # For now, let's assume they might also use SUMMARY_OUTPUT_TOKEN_TARGETS
            # Adjust if topic summary targets are defined differently
            return self.SUMMARY_OUTPUT_TOKEN_TARGETS.get(audience_type, {}).get(
                summary_size_key,
                self.SUMMARY_OUTPUT_TOKEN_TARGETS.get("technical", {}).get("L", 800),
            )
        else:
            return self.SUMMARY_OUTPUT_TOKEN_TARGETS.get(audience_type, {}).get(
                summary_size_key,
                self.SUMMARY_OUTPUT_TOKEN_TARGETS.get("technical", {}).get("M", 500),
            )

    def _estimate_prompt_overhead(self, audience_type: str, is_topic_summary: bool) -> int:
        """Estimates the token overhead for the prompt template."""
        active_prompts = AUDIENCE_PROMPT_MAP.get(audience_type, AUDIENCE_PROMPT_MAP["technical"])
        prompt_template = active_prompts["topic"] if is_topic_summary else active_prompts["paper"]

        # Estimate based on base overhead + length of the specific template being used
        # This is a rough estimate; more sophisticated token counting could be used if needed.
        template_specific_tokens = len(prompt_template) // self.AVG_CHARS_PER_TOKEN
        return self.PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS + template_specific_tokens

    def _get_max_content_snippet_chars(
        self, audience_type: str, summary_size_key: str, is_topic_summary: bool = False
    ) -> int:
        """Calculates the maximum characters allowed for a content snippet based on model limits."""
        output_token_target = self._get_output_token_target(
            audience_type, summary_size_key, is_topic_summary
        )
        prompt_overhead = self._estimate_prompt_overhead(audience_type, is_topic_summary)

        # Available tokens for the snippet itself
        available_for_snippet_tokens = (
            self.selected_model_input_token_limit
            - prompt_overhead
            - output_token_target
            - self.SAFETY_MARGIN_TOKENS
        )

        if available_for_snippet_tokens <= 0:
            logger.warning(
                f"Calculated available tokens for snippet is non-positive ({available_for_snippet_tokens}). "
                f"Model: {self.selected_model_api_id}, Limit: {self.selected_model_input_token_limit}, "
                f"Overhead: {prompt_overhead}, Output Target: {output_token_target}. "
                f"Defaulting to a small snippet size (1000 chars)."
            )
            # Fallback to a very small size if calculations lead to zero or negative tokens
            return 1000

        max_chars = available_for_snippet_tokens * self.AVG_CHARS_PER_TOKEN
        logger.info(
            f"Calculated MAX_CONTENT_SNIPPET_CHARS: {max_chars} (based on {available_for_snippet_tokens} available tokens). "
            f"Model Limit: {self.selected_model_input_token_limit}, Prompt Overhead: {prompt_overhead}, "
            f"Output Target: {output_token_target}"
        )

        # Apply the absolute cap from config
        absolute_max_cap = config.PAPER_MAX_CONTENT_SNIPPET_CHARS
        if max_chars > absolute_max_cap:
            logger.warning(
                f"Calculated max_chars ({max_chars}) exceeds absolute cap ({absolute_max_cap}). "
                f"Using cap value instead."
            )
            max_chars = absolute_max_cap

        return max_chars

    def _get_formatted_date_str(self, date_input: Optional[Union[str, datetime]]) -> str:
        if not date_input:  # Handles None or empty string
            return "N/A"
        # If a datetime comes in, format directly
        if isinstance(date_input, datetime):
            return date_input.strftime("%Y-%m-%d")
        # Otherwise attempt to parse the string safely
        try:
            return datetime.strptime(date_input, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return date_input

    # ------------------------------------------------------------------
    # Cache-key helpers
    # ------------------------------------------------------------------

    def _paper_cache_key(
        self,
        paper_id: str,
        summary_size_key: str,
        audience_type: str,
    ) -> str:
        """Stable key for per-paper summaries independent of prompt wording."""
        return f"paper:{paper_id}|size:{summary_size_key}|aud:{audience_type}|model:{self.selected_model_api_id}"

    def _topic_cache_key(
        self,
        query: str,
        summary_size_key: str,
        audience_type: str,
    ) -> str:
        qhash = hashlib.sha1(query.lower().encode()).hexdigest()
        return f"topic:{qhash}|size:{summary_size_key}|aud:{audience_type}|model:{self.selected_model_api_id}"

    async def _generate_single_summary(
        self,
        prompt: str,
        max_tokens: int,
        semaphore: Optional[asyncio.Semaphore] = None,
        system_prompt: Optional[str] = None,
        cache_key: str | None = None,
    ) -> str:
        import time  # timing for perf logs

        start_ts = time.perf_counter()

        # Check primary prompt-hash cache
        cached: Any | None = SummaryCache.get(self.selected_model_api_id, prompt, system_prompt)
        if cached is None and cache_key:
            cached = SummaryCache.get_generic(cache_key)

        if cached is not None:
            # Optional UX: introduce small artificial delay so spinner is visible
            delay = max(0.0, config.CACHED_SUMMARY_DELAY_SECONDS)
            if delay:
                await asyncio.sleep(delay)
            dur = time.perf_counter() - start_ts
            logger.info(
                "Summary cache hit | provider=%s model=%s prompt_len=%d duration=%.2fs",
                self.selected_model_provider,
                self.selected_model_api_id,
                len(prompt),
                dur,
            )
            return cast(str, cached)

        async def _call_llm() -> str:
            result = await self.llm_service.generate_completion_safe(
                provider_name=self.selected_model_provider,
                model_name=self.selected_model_api_id,
                prompt=prompt,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )
            if not result["ok"]:
                logger.error(f"LLM generation failed: {result['content']}")
            content = cast(str, result["content"])
            # Store in cache (best-effort)
            SummaryCache.set(self.selected_model_api_id, prompt, system_prompt, content)
            if cache_key:
                SummaryCache.set_generic(cache_key, content)
            return content

        timeout_sec = config.LLM_TIMEOUT_SECONDS
        try:
            if semaphore is not None:
                async with semaphore:
                    content = await asyncio.wait_for(_call_llm(), timeout=timeout_sec)
            else:
                content = await asyncio.wait_for(_call_llm(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.error(
                "LLM call timed out after %s seconds | provider=%s model=%s",
                timeout_sec,
                self.selected_model_provider,
                self.selected_model_api_id,
            )
            return "Error: LLM generation timed out."

        dur = time.perf_counter() - start_ts
        logger.info(
            "Summary generated | provider=%s model=%s prompt_len=%d max_out_tok=%d duration=%.2fs",
            self.selected_model_provider,
            self.selected_model_api_id,
            len(prompt),
            max_tokens,
            dur,
        )
        return cast(str, content)

    async def generate_report_html(
        self,
        query_or_url: str,
        papers_to_summarize: list[Paper],
        audience_type: str = "technical",
        summary_size: str = "M",
        is_url_input: bool = False,
        main_input_paper: Optional[Paper] = None,
        selected_similar_papers_data: Optional[list[Paper]] = None,
        papers_for_topic_report: Optional[list[Paper]] = None,
    ) -> str:
        _selected_similar_papers = (
            selected_similar_papers_data if selected_similar_papers_data is not None else []
        )
        _papers_for_topic_report = (
            papers_for_topic_report if papers_for_topic_report is not None else []
        )

        if is_url_input:
            if main_input_paper is None and not _selected_similar_papers and papers_to_summarize:
                _selected_similar_papers = papers_to_summarize
        elif papers_for_topic_report is None and papers_to_summarize:
            _papers_for_topic_report = papers_to_summarize

        # ------------------------------------------------------------------
        # Pre-fetch full text for all papers that will appear in the report
        # ------------------------------------------------------------------
        try:
            from infra.text_extractor import TextExtractor

            extractor = TextExtractor.instance()
            # Flatten the list of papers that need summaries
            papers_needing_text: list[Paper] = []
            if is_url_input:
                if main_input_paper:
                    papers_needing_text.append(main_input_paper)
                papers_needing_text.extend(_selected_similar_papers)
            else:
                papers_needing_text.extend(_papers_for_topic_report)

            # Remove dupes while preserving order
            seen_ids: set[str] = set()
            unique_papers: list[Paper] = []
            for p in papers_needing_text:
                if p.id not in seen_ids:
                    unique_papers.append(p)
                    seen_ids.add(p.id)

            # Launch extraction tasks with concurrency guard
            sem = asyncio.Semaphore(3)

            async def _bounded_extract(url: str, pid: str) -> None:
                async with sem:
                    path = await extractor.ensure_cached(url, pid)
                    # If we managed to cache and paper lacks text_content, load first chunk
                    if path:
                        paper_obj = next((pp for pp in unique_papers if pp.id == pid), None)
                        if paper_obj and (
                            not paper_obj.text_content or len(paper_obj.text_content) < 200
                        ):
                            try:
                                txt = await extractor.read_full_text(pid)
                                if txt:
                                    paper_obj.text_content = txt
                            except Exception:
                                pass

            await asyncio.gather(*[_bounded_extract(str(p.url), p.id) for p in unique_papers])

            # Attach full_text_path for later use (chat, etc.)
            for p in unique_papers:
                cached_path = await extractor.load_full_text(p.id)
                if cached_path:
                    p.full_text_path = cached_path
        except Exception as prep_exc:  # noqa: BLE001
            logger.warning("Pre-fetching full text failed: %s", prep_exc, exc_info=True)

        return await self._generate_report_html_async(
            original_query=query_or_url,
            audience_type=audience_type,
            summary_size=summary_size,
            is_url_input=is_url_input,
            main_input_paper=main_input_paper,
            similar_papers_data=_selected_similar_papers,
            papers_for_topic_report=_papers_for_topic_report,
        )

    async def _generate_report_html_async(
        self,
        original_query: str,
        audience_type: str,
        summary_size: str,
        is_url_input: bool,
        main_input_paper: Optional[Paper] = None,
        similar_papers_data: Optional[list[Paper]] = None,
        papers_for_topic_report: Optional[list[Paper]] = None,
    ) -> str:
        similar_papers_data = similar_papers_data if similar_papers_data is not None else []
        papers_for_topic_report = (
            papers_for_topic_report if papers_for_topic_report is not None else []
        )

        logger.info(
            f"ReportGenerator: Starting async report generation. Input: '{html.escape(original_query)}', Is URL: {is_url_input}, "
            f"Audience: {audience_type}, Size Key: {summary_size}, "
            f"Main Paper: {'Provided' if main_input_paper else 'None'}, "
            f"Similar Selected: {len(similar_papers_data)}, Topic Papers: {len(papers_for_topic_report)}"
        )
        report_parts = []

        active_prompts = AUDIENCE_PROMPT_MAP.get(audience_type, AUDIENCE_PROMPT_MAP["technical"])
        active_paper_prompt_template = active_prompts["paper"]

        user_summary_size_key = summary_size.upper()
        if user_summary_size_key not in self.QUALITATIVE_LENGTH_GUIDANCE:
            logger.warning(
                f"Invalid summary_size_key '{user_summary_size_key}', defaulting to 'M'."
            )
            user_summary_size_key = "M"

        topic_size_map = {"S": "M", "M": "L", "L": "XL", "XL": "XL"}
        actual_topic_summary_size_key = topic_size_map.get(user_summary_size_key, "L")

        topic_length_guidance = self.QUALITATIVE_LENGTH_GUIDANCE[actual_topic_summary_size_key]
        paper_length_guidance = self.QUALITATIVE_LENGTH_GUIDANCE[user_summary_size_key]

        paper_max_content_snippet_chars = self._get_max_content_snippet_chars(
            audience_type, user_summary_size_key, is_topic_summary=False
        )
        topic_max_content_snippet_chars = self._get_max_content_snippet_chars(
            audience_type, actual_topic_summary_size_key, is_topic_summary=True
        )

        paper_summary_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PAPER_SUMMARIES)

        # We'll prepend H1 and dynamic title later for correct ordering
        # report_parts.append("<h1>Research Report</h1>")

        # Show section headings only for query workflow (not for direct URL input)
        include_section_headings = not is_url_input

        # Informative notice only for the URL workflow when nothing else to summarise
        if is_url_input and main_input_paper is None and not similar_papers_data:
            report_parts.append(
                "<p>No specific papers were selected or provided for summary based on the input URL.</p>"
            )

        # -------------------- Concurrent main + similar paper summaries --------------------
        # Use tasks to satisfy mypy (coroutines wrapped via create_task)
        summary_tasks: list[asyncio.Task[str]] = []
        task_infos: list[tuple[Optional[Paper], str]] = []  # (paper_or_none, role)

        if main_input_paper:
            if include_section_headings:
                report_parts.append("<h3>Summary of Input Paper</h3>")
            main_snippet = (main_input_paper.text_content or "")[:paper_max_content_snippet_chars]
            fallback_note = ""
            if len(main_snippet.strip()) < 200:
                fallback_note = "NOTE: Full text was unavailable; summary must rely on limited context (abstract/metadata).\n\n"
            main_prompt = fallback_note + active_paper_prompt_template.format(
                paper_title=html.escape(main_input_paper.title or "N/A"),
                paper_authors=html.escape(
                    ", ".join(main_input_paper.authors) if main_input_paper.authors else "N/A"
                ),
                paper_publication_date=html.escape(
                    self._get_formatted_date_str(main_input_paper.published_date)
                ),
                paper_content_snippet=main_snippet,
                overall_instructions=paper_length_guidance,
                target_audience=audience_type,
            )
            main_tok_target = self._get_output_token_target(
                audience_type, user_summary_size_key, is_topic_summary=False
            )
            summary_tasks.append(
                asyncio.create_task(
                    self._generate_single_summary(
                        main_prompt,
                        main_tok_target,
                        semaphore=paper_summary_semaphore,
                        cache_key=self._paper_cache_key(
                            main_input_paper.id,
                            user_summary_size_key,
                            audience_type,
                        ),
                    )
                )
            )
            task_infos.append((main_input_paper, "main"))

        if similar_papers_data:
            if include_section_headings:
                report_parts.append("<h3>Summaries of Similar Papers</h3>")
            for paper in similar_papers_data:
                snip = (paper.text_content or "")[:paper_max_content_snippet_chars]
                fallback_note = ""
                if len(snip.strip()) < 200:
                    fallback_note = "NOTE: Full text was unavailable; summary must rely on limited context (abstract/metadata).\n\n"
                paper_prompt = fallback_note + active_paper_prompt_template.format(
                    paper_title=html.escape(paper.title or "N/A"),
                    paper_authors=html.escape(", ".join(paper.authors) if paper.authors else "N/A"),
                    paper_publication_date=html.escape(
                        self._get_formatted_date_str(paper.published_date)
                    ),
                    paper_content_snippet=snip,
                    overall_instructions=paper_length_guidance,
                    target_audience=audience_type,
                )
                tok_target = self._get_output_token_target(
                    audience_type, user_summary_size_key, is_topic_summary=False
                )
                summary_tasks.append(
                    asyncio.create_task(
                        self._generate_single_summary(
                            paper_prompt,
                            tok_target,
                            semaphore=paper_summary_semaphore,
                            cache_key=self._paper_cache_key(
                                paper.id,
                                user_summary_size_key,
                                audience_type,
                            ),
                        )
                    )
                )
                task_infos.append((paper, "similar"))

        # Topic overview – queue BEFORE similar paper summaries so the overview appears first
        if papers_for_topic_report:
            consolidated_content_for_topic = ""
            for _p in papers_for_topic_report:
                t_title = html.escape(_p.title or "N/A")
                a_list = _p.authors if _p.authors else []
                a_str = (
                    f"{html.escape(', '.join(a_list[: config.MAX_AUTHORS_DISPLAY]))}, et al."
                    if a_list and len(a_list) > config.MAX_AUTHORS_DISPLAY
                    else html.escape(", ".join(a_list))
                    if a_list
                    else "N/A"
                )
                d_str = html.escape(self._get_formatted_date_str(_p.published_date))
                snip = (_p.text_content or "")[:topic_max_content_snippet_chars]
                consolidated_content_for_topic += f"Title: {t_title}\nAuthors: {a_str}\nPublication Date: {d_str}\nContent Snippet: {html.escape(snip)}\n\n---\n\n"

            if consolidated_content_for_topic.strip():
                system_prompt_topic, user_prompt_topic = get_topic_analysis_prompts(
                    topic=original_query,
                    num_papers_to_synthesize=len(papers_for_topic_report),
                    target_audience=audience_type,
                    context_snippets=consolidated_content_for_topic,
                    overall_instructions=topic_length_guidance,
                    summary_size_key=actual_topic_summary_size_key,
                )
                tok_target_topic = self._get_output_token_target(
                    audience_type, actual_topic_summary_size_key, is_topic_summary=True
                )
                summary_tasks.append(
                    asyncio.create_task(
                        self._generate_single_summary(
                            user_prompt_topic,
                            tok_target_topic,
                            system_prompt=system_prompt_topic,
                            cache_key=self._topic_cache_key(
                                original_query,
                                actual_topic_summary_size_key,
                                audience_type,
                            ),
                        )
                    )
                )
                task_infos.append((None, "topic_overview"))

        # Run all queued LLM calls concurrently and assemble results in original order
        if summary_tasks:
            summary_results = await asyncio.gather(*summary_tasks, return_exceptions=True)
            for (paper_obj, role), result in zip(task_infos, summary_results):
                if role == "topic_overview":
                    if include_section_headings:
                        report_parts.append("<h3>Overview</h3>")
                    if isinstance(result, Exception):
                        logger.error("Error generating topic overview: %s", result, exc_info=result)
                        report_parts.append(
                            "<p><em>Could not generate topic overview due to an error.</em></p>"
                        )
                    else:
                        overview_text = cast(str, result)
                        report_parts.append(
                            f"<div class='topic-summary'>{self._text_to_html_paragraphs(overview_text)}</div>"
                        )
                    # After overview, maybe heading for following paper summaries
                    if include_section_headings and similar_papers_data:
                        report_parts.append("<h3>Summaries of Similar Papers</h3>")
                    continue

                report_parts.append(
                    f"<div><h4><a href='{html.escape(str(paper_obj.url) if paper_obj and paper_obj.url else '#')}' target='_blank' rel='noopener noreferrer'>{html.escape(paper_obj.title or 'N/A' if paper_obj else 'N/A')}</a></h4>"
                )
                if isinstance(result, Exception):
                    logger.error(
                        "Error generating summary for paper %s: %s",
                        paper_obj.id if paper_obj else "N/A",
                        result,
                        exc_info=result,
                    )
                    report_parts.append(
                        "<p><em>Summary could not be generated for this paper due to an error.</em></p>"
                    )
                else:
                    # At this point mypy still sees result as str | BaseException; but we already handled
                    assert isinstance(result, str)
                    report_parts.append(self._text_to_html_paragraphs(result))
                paper_id_safe = paper_obj.id if paper_obj else ""
                # Safe encode title & URL
                title_q = urllib.parse.quote_plus(
                    paper_obj.title if paper_obj and paper_obj.title else ""
                )
                url_q = urllib.parse.quote_plus(
                    str(paper_obj.url) if paper_obj and paper_obj.url else ""
                )
                report_parts.append(
                    f"<button class='ask-assistant-btn' hx-get='/chat-modal?paper_id={paper_id_safe}&title={title_q}&paper_url={url_q}' hx-target='#modal-container' hx-swap='innerHTML'>Ask Assistant About Paper</button>"
                )
                report_parts.append("</div>")

        report_body = "".join(report_parts)

        # Dynamically generate the title based on the report content
        title = await self._generate_report_title(report_body, self.selected_model_api_id)

        # Remove common artifacts from LLM-generated titles (e.g., 'TITLE:', markdown asterisks)
        clean_title = re.sub(r"(?i)^\s*title\s*[:\-]?\s*", "", title).strip()
        clean_title = cast(str, clean_title).strip('* "')

        # --- Copy-text utility only (works even on non-secure origins) -----------
        script_html = """
<script>
function copyReportText() {
    try {
        const reportEl = document.getElementById('lux-report');
        if (!reportEl) {
            alert('Copy failed: Report element not found.');
            return;
        }
        const text = reportEl.innerText;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(() => alert('Report text copied!'))
                .catch(() => fallbackCopy(text));
        } else {
            fallbackCopy(text);
        }
    } catch (err) {
        console.error('copyReportText unexpected error', err);
        alert('Could not copy text.');
    }
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
        const successful = document.execCommand('copy');
        if (successful) {
            alert('Report text copied!');
        } else {
            alert('Copy failed. Select text manually.');
        }
    } catch (e) {
        alert('Copy failed. Select text manually.');
    }
    document.body.removeChild(ta);
}
</script>
"""

        # Action buttons (appear before report content)
        buttons_html = (
            "<div class='report-actions' style='margin:1em 0;'>"
            "<button type='button' onclick='copyReportText()'>Copy&nbsp;Text</button>"
            "</div>"
        )

        # Assemble full report body with headings in correct order
        full_body = (
            f"<h1>Research Report</h1>"
            f"<h2 style='margin-top:0'>{html.escape(clean_title)}</h2>"
            f"{report_body}"
        )

        wrapped_report = f"<div id='lux-report'>{full_body}</div>"

        # script first (hidden), then copy button, then report
        return script_html + buttons_html + wrapped_report

    async def _generate_report_title(self, report_content: str, model_key: str) -> str:
        """Generates a concise, intelligent title for the report content."""
        if not report_content.strip():
            return "Consolidated Research Report"

        prompt = (
            f"Based on the following research summary, please generate a concise and informative title. "
            f"The title should be no more than 10 words and capture the main theme of the text.\n\n"
            f"SUMMARY:\n{report_content[:2000]}...\n\nTITLE:"
        )

        result = await self.llm_service.generate_completion_safe(
            provider_name=self.selected_model_provider,
            model_name=self.selected_model_api_id,
            prompt=prompt,
            max_tokens=30,
            temperature=0.4,
        )
        if not result["ok"]:
            logger.warning("Dynamic title generation failed: %s", result["content"])
            raise RuntimeError(result["content"])

        # Clean up the title
        return cast(str, result["content"]).strip().replace('"', "")

    async def generate_direct_query_answer(
        self,
        query: str,
        audience_type: str = "technical",
        num_supporting_papers: int = 3,
        max_tokens: int | None = None,
    ) -> str:
        """Answer a direct research question without collecting papers first.

        This lightweight path is useful when the user asks an explicit question
        rather than requesting a topic landscape or paper summaries.
        """
        # Build a simple direct-answer prompt (no enforced structure)
        # Use the shared default prompt then tailor it.

        audience_cfg = AUDIENCE_PROMPT_MAP.get(audience_type, AUDIENCE_PROMPT_MAP["technical"])
        system_prompt = (
            DEFAULT_SYSTEM_PROMPT
            + " "
            + audience_cfg["system_suffix"]
            + " Respond with a direct answer in plain paragraphs. Do NOT output section headings, bullet points, or numbered lists."
        )

        # --------------------------------------------------------------
        # Attempt to ground answer with Exa's /answer endpoint
        # --------------------------------------------------------------
        exa_answer: dict[str, Any] = {}
        try:
            loop = asyncio.get_running_loop()
            exa_answer = await loop.run_in_executor(
                None, partial(self.exa_client.answer, query, top_k=num_supporting_papers)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Exa answer lookup failed: %s", exc, exc_info=True)

        passages_block = ""
        if exa_answer and exa_answer.get("passages"):
            lines: list[str] = []
            for idx, p in enumerate(exa_answer["passages"], 1):
                snippet = p.get("snippet", "").replace("\n", " ").strip()
                url = p.get("url", "")
                lines.append(f"[{idx}] {snippet} (Source: {url})")
            passages_block = "\n\nSOURCE PASSAGES:\n" + "\n".join(lines)

        user_prompt = (
            "You are answering a direct research question for a user. "
            "Provide the best possible answer grounded in the provided source passages if they are relevant. "
            "If the passages are not relevant you may rely on your own knowledge. "
            "Answer in no more than 250 words (≈ one to two short paragraphs).\n\n"
            f"Question: {query.strip()}"
            f"{passages_block}"
        )

        # Enforce a hard upper bound so answers stay brief
        HARD_CAP_TOKENS = 320
        max_tokens = HARD_CAP_TOKENS if max_tokens is None else min(max_tokens, HARD_CAP_TOKENS)

        start_time = time.time()
        result = await self.llm_service.generate_completion_safe(
            provider_name=self.selected_model_provider,
            model_name=self.selected_model_api_id,
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=0.5,
        )
        elapsed_time = time.time() - start_time
        logger.info(f"Direct query generation took {elapsed_time:.2f} seconds")

        if not result["ok"]:
            logger.error("Direct query generation failed: %s", result["content"])
            answer_html = f"<div class='error-banner'>Error: {html.escape(result['content'])}</div>"
        else:
            # Clean common markdown markup (bullets, *italic*, **bold**)
            cleaned_text = result["content"].strip()  # <--- Strip whitespace from result
            # Remove leading bullet markers ('* ' or '- ' or numbered lists like '1. ')
            cleaned_text = re.sub(r"^\s*([*\-]|\d+\.)\s+", "", cleaned_text, flags=re.MULTILINE)
            # Strip emphasis markers while keeping the text
            cleaned_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned_text)  # **bold** -> text
            cleaned_text = re.sub(r"\*([^*]+)\*", r"\1", cleaned_text)  # *italic* -> text
            # Also strip any remaining stray asterisks that aren't in math/code (best-effort)
            cleaned_text = cleaned_text.replace("*", "")
            html_body = self._text_to_html_paragraphs(cleaned_text)
            answer_container = f"<div id='lux-answer' class='direct-query-answer'>{html_body}</div>"
            answer_html = answer_container

        return answer_html

    def _text_to_html_paragraphs(self, text: str) -> str:
        if not text or not text.strip():
            return "<p><em>Summary could not be generated or is empty.</em></p>"

        escaped_text = html.escape(text)  # <--- Escape the full text first
        paragraphs = [p.strip() for p in escaped_text.split("\n") if p.strip()]
        html_paragraphs = [f"<p>{p}</p>" for p in paragraphs if p]
        if not html_paragraphs:
            return "<p><em>Summary content appears to be empty after formatting.</em></p>"
        return "\n".join(html_paragraphs)

    def _format_date(self, date_str: Optional[Union[str, datetime]]) -> Optional[str]:
        if not date_str:
            return None
        if isinstance(date_str, datetime):
            return date_str.strftime("%B %d, %Y")
        try:
            # Using '%Y-%m-%d' for output to match original apparent intent.
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            try:
                dt_obj = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                return dt_obj.strftime("%B %d, %Y")
            except ValueError:
                try:
                    dt_obj = datetime.strptime(date_str, "%B %d, %Y")
                    return dt_obj.strftime("%B %d, %Y")
                except ValueError:
                    logger.warning(f"Unrecognized date string format: {date_str}. Returning as is.")
                    return date_str
