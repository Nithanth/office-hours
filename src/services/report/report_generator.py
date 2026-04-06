"""Report generation service. Generates per-paper and topic summaries by orchestrating LLM calls and
Exa searches."""

import asyncio
import hashlib
import html
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any, Optional, Union, cast

from loguru import logger

from config import config
from models.paper import Paper
from prompts.prompt_builder import AUDIENCE_PROMPT_MAP
from prompts.prompt_utils import max_content_snippet_chars as _calc_snippet_chars
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMService
from services.core.llm_utils.types import ProviderName
from services.paper.embedding import embed_query
from services.report.report_utils import PaperSummariser, SummaryCache, TopicSummariser
from utils.html_builder import text_to_html_paragraphs

logger = logger.bind(component="ReportGenerator")


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
        selected_model_provider: ProviderName,
        selected_model_input_token_limit: int,
    ) -> None:
        self.llm_service = llm_service
        self.exa_client = exa_client
        self.selected_model_api_id = selected_model_api_id
        self.selected_model_provider = selected_model_provider
        self.selected_model_input_token_limit = selected_model_input_token_limit
        self._paper_summariser = PaperSummariser(
            selected_model_input_token_limit=selected_model_input_token_limit,
        )
        self._topic_summariser = TopicSummariser(
            selected_model_input_token_limit=selected_model_input_token_limit,
        )

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
        """Proxy to shared `prompt_utils.max_content_snippet_chars`."""

        return cast(
            int,
            _calc_snippet_chars(
                selected_model_input_token_limit=self.selected_model_input_token_limit,
                audience_type=audience_type,
                summary_size_key=summary_size_key,
                is_topic_summary=is_topic_summary,
            ),
        )

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
            logger.error("unrecognized date string format", date_str=date_input, exc_info=True)
            return date_input

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
                "Summary cache hit | provider={self.selected_model_provider} model={self.selected_model_api_id} prompt_len={len(prompt)} duration={dur:.2f}"
            )
            return cast(str, cached)

        async def _call_llm() -> str:
            # Build final prompt (custom user instructions feature removed)
            composed_prompt = prompt

            result = await self.llm_service.generate_completion_safe(
                provider_name=self.selected_model_provider,
                model_name=self.selected_model_api_id,
                prompt=composed_prompt,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
            )

            if not result["ok"]:
                logger.error("LLM generation failed", error=result["content"])

            content = cast(str, result["content"])

            # Store in cache (best-effort)
            SummaryCache.set(self.selected_model_api_id, composed_prompt, system_prompt, content)
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
                "llm call timed out",
                timeout=timeout_sec,
                provider=self.selected_model_provider,
                model=self.selected_model_api_id,
            )
            return "Error: LLM generation timed out."

        dur = time.perf_counter() - start_ts
        logger.info(
            "Summary generated",
            provider=self.selected_model_provider,
            model=self.selected_model_api_id,
            prompt_len=len(prompt),
            max_out_tok=max_tokens,
            duration=f"{dur:.2f}",
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

        try:
            from services.paper.text_extractor import TextExtractor

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
            sem = asyncio.Semaphore(config.PDF_EXTRACTION_CONCURRENCY)

            async def _bounded_extract(url: str, pid: str) -> None:
                async with sem:
                    path = await extractor.ensure_cached(url, pid)
                    # If we managed to cache and paper lacks text_content, load first chunk
                    if path:
                        paper_obj = next((pp for pp in unique_papers if pp.id == pid), None)
                        if paper_obj:
                            try:
                                txt = await extractor.read_full_text(pid)
                                if txt and len(txt) > len(paper_obj.text_content or ""):
                                    paper_obj.text_content = txt
                                    logger.info(
                                        "full_text_cached",
                                        paper_id=pid,
                                        chars=len(txt),
                                        prev_chars=len(paper_obj.text_content),
                                    )
                            except (OSError, UnicodeDecodeError) as extract_err:
                                logger.debug("extractor_read_failed", error=str(extract_err))

            await asyncio.gather(*[_bounded_extract(str(p.url), p.id) for p in unique_papers])

            # Attach full_text_path for later use (chat, etc.)
            for p in unique_papers:
                cached_path = await extractor.load_full_text(p.id)
                if cached_path:
                    p.full_text_path = cached_path

        except (OSError, RuntimeError, asyncio.CancelledError) as prep_exc:
            logger.warning("pre-fetch full text failed", error=str(prep_exc), exc_info=True)

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

        topic_size_map = {"S": "M", "M": "L", "L": "L", "XL": "L"}
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

        # Show section headings only for query workflow (not for direct URL input)
        include_section_headings = not is_url_input

        # Informative notice only for the URL workflow when nothing else to summarise
        if is_url_input and main_input_paper is None and not similar_papers_data:
            report_parts.append(
                "<p>No specific papers were selected or provided for summary based on the input URL.</p>"
            )

        # Log total vs snippet chars per paper once per report
        papers_for_stats: list[Paper] = []
        if main_input_paper is not None:
            papers_for_stats.append(main_input_paper)
        papers_for_stats.extend(similar_papers_data)
        if not is_url_input:
            papers_for_stats.extend(papers_for_topic_report)

        for p in papers_for_stats:
            total_chars = len(p.text_content or "")
            snippet_limit = paper_max_content_snippet_chars
            snippet_used = min(total_chars, snippet_limit)
            logger.info(
                f"report_paper_char_stats: id={p.id} total={total_chars} used={snippet_used}/{snippet_limit}",
                paper_id=p.id,
                total_chars=total_chars,
                snippet_used=snippet_used,
                snippet_limit=snippet_limit,
            )

        # -------------------- Concurrent main + similar paper summaries --------------------
        # Use tasks to satisfy mypy (coroutines wrapped via create_task)
        summary_tasks: list[asyncio.Task[str]] = []
        task_infos: list[tuple[Optional[Paper], str]] = []  # (paper_or_none, role)

        def _queue_paper_summary(paper: Paper, *, role: str) -> None:
            hybrid_needed = (
                len(paper.text_content or "") > config.HYBRID_FULL_CONTEXT_CHAR_THRESHOLD
            )
            snippet_override: str | None = None
            if hybrid_needed:
                snippet_override = self._build_hybrid_snippet(
                    paper,
                    char_limit=paper_max_content_snippet_chars,
                )

            paper_prompt, tok_target = self._paper_summariser.build_prompt_and_target(
                paper,
                audience_type=audience_type,
                summary_size_key=user_summary_size_key,
                length_guidance=paper_length_guidance,
                prompt_template=active_paper_prompt_template,
                snippet_char_limit=paper_max_content_snippet_chars,
                snippet_override=snippet_override,
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
            task_infos.append((paper, role))

        if main_input_paper:
            if include_section_headings:
                report_parts.append("<h3>Summary of Input Paper</h3>")
            _queue_paper_summary(main_input_paper, role="main")

        if similar_papers_data:
            if include_section_headings:
                report_parts.append("<h3>Summaries of Similar Papers</h3>")
            for paper in similar_papers_data:
                _queue_paper_summary(paper, role="similar")

        if papers_for_topic_report and not is_url_input:
            if include_section_headings:
                report_parts.append("<h3>Summaries of Selected Papers</h3>")
            for paper in papers_for_topic_report:
                _queue_paper_summary(paper, role="topic")

        # Topic overview / common theme --------------------------------------------------
        # 1) Query workflow → normal topic overview (is_url_input is False)
        # 2) URL workflow → optional common theme across *selected similar papers* when
        #    the feature flag is on and we have enough papers to synthesise.

        if (
            is_url_input
            and config.ENABLE_URL_SIMILAR_OVERVIEW
            and len(similar_papers_data) >= config.URL_THEME_MIN_PAPERS
        ):
            # Build a concise theme overview from the selected similar papers.
            report_parts.insert(0, "<h3>Common Theme Across Selected Papers</h3>")

            (system_prompt_url_theme, user_prompt_url_theme), tok_target_theme = (
                self._topic_summariser.build_prompts_and_target(
                    original_query=original_query,
                    papers=similar_papers_data,
                    audience_type=audience_type,
                    summary_size_key=user_summary_size_key,
                    length_guidance=topic_length_guidance,
                    snippet_char_limit=topic_max_content_snippet_chars,
                )
            )

            summary_tasks.insert(
                0,
                asyncio.create_task(
                    self._generate_single_summary(
                        user_prompt_url_theme,
                        tok_target_theme,
                        semaphore=paper_summary_semaphore,
                        system_prompt=system_prompt_url_theme,
                        cache_key=self._topic_cache_key(
                            f"url_theme:{'|'.join(p.id for p in similar_papers_data)}",
                            user_summary_size_key,
                            audience_type,
                        ),
                    )
                ),
            )
            task_infos.insert(0, (None, "url_theme"))

        # Query workflow topic overview
        logger.debug(
            "topic_overview_check",
            papers_for_topic_report_count=len(papers_for_topic_report),
            is_url_input=is_url_input,
            will_trigger=bool(papers_for_topic_report and not is_url_input),
        )
        if papers_for_topic_report and not is_url_input:
            logger.info("topic_overview_triggered", papers_count=len(papers_for_topic_report))
            (system_prompt_topic, user_prompt_topic), tok_target_topic = (
                self._topic_summariser.build_prompts_and_target(
                    original_query=original_query,
                    papers=papers_for_topic_report,
                    audience_type=audience_type,
                    summary_size_key=actual_topic_summary_size_key,
                    length_guidance=topic_length_guidance,
                    snippet_char_limit=topic_max_content_snippet_chars,
                )
            )

            summary_tasks.append(
                asyncio.create_task(
                    self._generate_single_summary(
                        user_prompt_topic,
                        tok_target_topic,
                        semaphore=paper_summary_semaphore,
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
                        logger.error(
                            "topic overview generation error", error=str(result), exc_info=True
                        )
                        report_parts.append(
                            "<p><em>Could not generate topic overview due to an error.</em></p>"
                        )
                    else:
                        overview_text = cast(str, result)
                        report_parts.append(
                            f"<div class='topic-summary'>{text_to_html_paragraphs(overview_text)}</div>"
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
                        "paper summary error",
                        paper_id=paper_obj.id if paper_obj else "N/A",
                        error=str(result),
                        exc_info=True,
                    )
                    report_parts.append(
                        "<p><em>Summary could not be generated for this paper due to an error.</em></p>"
                    )
                else:
                    # At this point mypy still sees result as str | BaseException; but we already handled
                    assert isinstance(result, str)
                    report_parts.append(text_to_html_paragraphs(result))
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

        title_html = html.escape(clean_title) if clean_title else "Research Report"

        actions_html = (
            "<div id='report-actions' class='mt-4 pt-4 border-t border-gray-700 flex justify-end gap-2'>"
            "<button id='report-notes-btn' class='btn-secondary' title='Generate Notes' type='button'>"
            "<i class='fa-solid fa-wand-magic-sparkles'></i>&nbsp;Generate Notes"
            "</button>"
            "<button id='copy-report-btn' class='copy-icon-btn' title='Copy text' type='button'>"
            "<i class='fa-solid fa-copy'></i>"
            "</button>"
            "</div>"
        )

        return (
            "<div id='report-content' class='mt-6 p-6 border border-gray-700 rounded-lg bg-gray-800/50'>"
            f"<h2 class='text-2xl font-bold mb-4'>{title_html}</h2>"
            f"{report_body}"
            f"{actions_html}"
            "</div>"
        )

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
            logger.warning("dynamic title generation failed", error=result["content"])
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
        """Answer a direct research question by delegating to DirectQueryService."""
        from services.query.direct_query_service import DirectQueryService

        svc = DirectQueryService(
            llm_service=self.llm_service,
            exa_client=self.exa_client,
            selected_model_api_id=self.selected_model_api_id,
            selected_model_provider=self.selected_model_provider,
        )
        result = await svc.generate(
            query,
            audience_type=audience_type,
            num_supporting_papers=num_supporting_papers,
            max_tokens=max_tokens,
        )
        return cast(str, result)

    def _text_to_html_paragraphs(self, text: str) -> str:
        """Return HTML markup for *text*, wrapping each paragraph in <p> tags."""

        return cast(str, text_to_html_paragraphs(text))

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
                    logger.warning("unrecognized date string format", date_str=date_str)
                    return date_str

    # ---------------------------------------------------------------------
    # Hybrid snippet preparation
    # ---------------------------------------------------------------------

    def _build_hybrid_snippet(self, paper: Paper, *, char_limit: int) -> str:
        """Return RAG-driven evidence snippet (abstract + themed chunks)."""

        themes: dict[str, str] = {
            "Motivation": "What problem does the paper solve and why is it important?",
            "Method": "How does the paper’s method work?",
            "Results": "What are the key results and limitations?",
        }

        parts: list[str] = []

        # -------- abstract extraction (very naive) --------
        abstract_text = ""
        if paper.text_content:
            m = re.search(r"(?is)abstract[:\s]*(.+?)(?:\n\s*\n|$)", paper.text_content[:8000])
            abstract_text = m.group(1).strip() if m else paper.text_content[:1200].strip()

        if abstract_text:
            parts.append("ABSTRACT:\n" + abstract_text)

        # Ensure vector index exists
        try:
            from services.paper import vector_store  # local import avoids early binding

            if paper.text_content and not vector_store.index_exists(paper.id):
                vector_store.build_index_from_text(paper.id, paper.text_content)
        except Exception as exc:
            logger.error("vector_index_build_failed", paper_id=paper.id, err=str(exc))

        # RAG retrieval per theme
        for theme, q in themes.items():
            try:
                from services.paper import vector_store  # late import for stubbing

                q_vec = embed_query(q)
                hits = vector_store.search(
                    paper.id,
                    q_vec,
                    query_text=q,
                    top_k=config.HYBRID_RAG_TOPK,
                )
                if hits:
                    joined = "\n".join(h["text"].strip() for h in hits)
                    parts.append(f"{theme.upper()}:\n" + joined)
            except Exception as exc:
                logger.error("hybrid_rag_error", theme=theme, paper_id=paper.id, err=str(exc))

        snippet = "\n\n".join(parts)
        return snippet[:char_limit]
