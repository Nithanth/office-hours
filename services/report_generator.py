import asyncio
import logging
from typing import List, Dict, Any, Optional, Union
from datetime import datetime
from services.llm_client import LLMService, LLMProviderName, get_llm_service
from services.exa_client import ExaClient
from models.paper import Paper
from config import config

logger = logging.getLogger(__name__)

class ReportGenerator:
    # Average characters per token, can be adjusted based on observations
    AVG_CHARS_PER_TOKEN: int = 4
    # Estimated token overhead for the static parts of the prompt template (instructions, boilerplate, etc.)
    PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS: int = 350 # Increased slightly for robust estimation
    # Additional safety buffer in token calculations
    SAFETY_MARGIN_TOKENS: int = 150 # Increased slightly

    # These should ideally match or be derived from config.py if defined there
    # For simplicity here, using the structure from your previous attempt.
    # Ensure these are consistent with your config.py
    QUALITATIVE_LENGTH_GUIDANCE: Dict[str, str] = config.QUALITATIVE_LENGTH_GUIDANCE
    SUMMARY_OUTPUT_TOKEN_TARGETS: Dict[str, Dict[str, int]] = config.SUMMARY_OUTPUT_TOKEN_TARGETS

    AUDIENCE_PROMPT_MAP = {
        "technical": {
            "topic": config.TOPIC_SUMMARY_PROMPT_TEMPLATE_TECHNICAL,
            "paper": config.PAPER_SUMMARY_PROMPT_TEMPLATE_TECHNICAL,
        },
        "semi_technical": {
            "topic": config.TOPIC_SUMMARY_PROMPT_SEMI_TECH,
            "paper": config.PAPER_SUMMARY_PROMPT_SEMI_TECH,
        },
        "non_technical": {
            "topic": config.TOPIC_SUMMARY_PROMPT_NON_TECH,
            "paper": config.PAPER_SUMMARY_PROMPT_NON_TECH,
        },
    }

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

    def _get_output_token_target(self, audience_type: str, summary_size_key: str, is_topic_summary: bool) -> int:
        """Helper to get the target output token count for a summary."""
        if is_topic_summary:
            # Topic summaries might have different sizing logic or use a different part of the config
            # For now, let's assume they might also use SUMMARY_OUTPUT_TOKEN_TARGETS
            # Adjust if topic summary targets are defined differently
            return self.SUMMARY_OUTPUT_TOKEN_TARGETS.get(audience_type, {}).get(
                summary_size_key, self.SUMMARY_OUTPUT_TOKEN_TARGETS.get("technical", {}).get("L", 800)
            )
        else:
            return self.SUMMARY_OUTPUT_TOKEN_TARGETS.get(audience_type, {}).get(
                summary_size_key, self.SUMMARY_OUTPUT_TOKEN_TARGETS.get("technical", {}).get("M", 500)
            )

    def _estimate_prompt_overhead(self, audience_type: str, is_topic_summary: bool) -> int:
        """Estimates the token overhead for the prompt template."""
        active_prompts = self.AUDIENCE_PROMPT_MAP.get(audience_type, self.AUDIENCE_PROMPT_MAP["technical"])
        prompt_template = active_prompts["topic"] if is_topic_summary else active_prompts["paper"]
        
        # Estimate based on base overhead + length of the specific template being used
        # This is a rough estimate; more sophisticated token counting could be used if needed.
        template_specific_tokens = len(prompt_template) // self.AVG_CHARS_PER_TOKEN
        return self.PROMPT_TEMPLATE_BASE_OVERHEAD_TOKENS + template_specific_tokens

    def _get_max_content_snippet_chars(self, audience_type: str, summary_size_key: str, is_topic_summary: bool = False) -> int:
        """Calculates the maximum characters allowed for a content snippet based on model limits."""
        output_token_target = self._get_output_token_target(audience_type, summary_size_key, is_topic_summary)
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
        return max_chars

    async def _generate_single_summary(
        self, 
        prompt: str, 
        max_tokens: int, 
        semaphore: Optional[asyncio.Semaphore] = None
    ) -> str:
        if semaphore:
            async with semaphore:
                try:
                    response = await self.llm_service.generate_completion(
                        provider_name=self.selected_model_provider,
                        model_name=self.selected_model_api_id,
                        prompt=prompt,
                        max_tokens=max_tokens,
                    )
                    return response
                except Exception as e:
                    logger.error(f"Error generating summary with {self.selected_model_provider.value}/{self.selected_model_api_id} under semaphore: {e}", exc_info=True)
                    return f"Error: Could not generate summary. Details: {e}"
        else:
            try:
                response = await self.llm_service.generate_completion(
                    provider_name=self.selected_model_provider,
                    model_name=self.selected_model_api_id,
                    prompt=prompt,
                    max_tokens=max_tokens,
                )
                return response
            except Exception as e:
                logger.error(f"Error generating summary with {self.selected_model_provider.value}/{self.selected_model_api_id}: {e}", exc_info=True)
                return f"Error: Could not generate summary. Details: {e}"

    async def generate_report_html(
        self,
        query_or_url: str,
        papers_to_summarize: List[Paper],
        audience_type: str = "technical",
        summary_size: str = "M",
        is_url_input: bool = False, # Retained for logic, but might be derivable from exa_client interaction if needed
    ) -> str:
        return await self._generate_report_html_async(
            query_or_url, papers_to_summarize, audience_type, summary_size, is_url_input
        )

    async def _generate_report_html_async(
        self,
        query_or_url: str,
        papers_to_summarize: List[Paper],
        audience_type: str,
        summary_size: str,
        is_url_input: bool,
    ) -> str:
        logger.info(f"ReportGenerator: Starting async report generation. Input: '{query_or_url}', Is URL: {is_url_input}, Audience: {audience_type}, Size: {summary_size}, Papers: {len(papers_to_summarize)}")
        report_parts = []

        active_prompts = self.AUDIENCE_PROMPT_MAP.get(audience_type, self.AUDIENCE_PROMPT_MAP["technical"])
        active_topic_prompt_template = active_prompts["topic"]
        active_paper_prompt_template = active_prompts["paper"]

        user_summary_size_key = summary_size.upper()
        if user_summary_size_key not in self.QUALITATIVE_LENGTH_GUIDANCE:
            logger.warning(f"Invalid summary_size '{summary_size}', defaulting to 'M'.")
            user_summary_size_key = "M"

        topic_size_map = {'S': 'M', 'M': 'L', 'L': 'XL', 'XL': 'XL'}
        actual_topic_summary_size_key = topic_size_map.get(user_summary_size_key, 'L')
        
        topic_length_guidance = self.QUALITATIVE_LENGTH_GUIDANCE[actual_topic_summary_size_key]
        paper_length_guidance = self.QUALITATIVE_LENGTH_GUIDANCE[user_summary_size_key]

        paper_max_content_snippet_chars = self._get_max_content_snippet_chars(audience_type, user_summary_size_key, is_topic_summary=False)

        paper_summary_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_PAPER_SUMMARIES)

        report_parts.append("<h1>Research Report</h1>")
        # report_parts.append(f"<p><em>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</em></p>") # Removed as per user request
        # report_parts.append(f"<p><em>Audience: {audience_type.title()}, Summary Size: {user_summary_size_key.upper()}</em></p>") # Removed as per user request
        # report_parts.append(f"<p><em>Summary Size: {user_summary_size_key.upper()}</em></p>") # Also removing this line

        if is_url_input:
            report_parts.append(f"<h2>Summaries of Research Papers Similar to: <a href='{query_or_url}' target='_blank'>{query_or_url}</a></h2>")
            # For URL input, we directly summarize similar papers. No topic/main paper summary.
            report_section_title = "Individual Summaries of Similar Papers"
            logger.info(f"ReportGenerator: URL input. Query/URL: '{query_or_url}'. Summarizing {len(papers_to_summarize)} similar papers.")
        else:
            # Topic/Query Input Workflow
            report_parts.append(f"<h2>Topic Overview: {query_or_url}</h2>")
            topic_max_tokens = self._get_output_token_target(audience_type, actual_topic_summary_size_key, is_topic_summary=True)
            topic_prompt = active_topic_prompt_template.format(
                query_text=query_or_url,
                target_audience=audience_type,
                overall_instructions=topic_length_guidance
            )
            logger.info(f"ReportGenerator: Generating TOPIC summary for query '{query_or_url}'. Max Tokens: {topic_max_tokens}.")
            topic_summary = await self._generate_single_summary(topic_prompt, topic_max_tokens)
            report_parts.append(self._text_to_html_paragraphs(topic_summary))
            report_parts.append("<hr>")
            report_section_title = "Supporting Research Papers"
            logger.info(f"ReportGenerator: Topic/Query input. Query: '{query_or_url}'. Summarizing {len(papers_to_summarize)} supporting papers.")

        if papers_to_summarize:
            report_parts.append(f"<h2>{report_section_title}</h2>")
            paper_max_tokens_individual = self._get_output_token_target(audience_type, user_summary_size_key, is_topic_summary=False)
            summary_tasks = []
            for paper in papers_to_summarize:
                full_text_snippet = paper.content if paper.content else (paper.abstract if paper.abstract else "Content not available.")
                
                if len(full_text_snippet) > paper_max_content_snippet_chars:
                    full_text_snippet = full_text_snippet[:paper_max_content_snippet_chars] + "... (content truncated)"
                    logger.info(f"Truncated content for paper '{paper.title}' to {paper_max_content_snippet_chars} chars.")
                
                paper_authors_str = ", ".join(paper.authors) if paper.authors else "N/A"
                paper_pub_date_str = self._format_date(paper.published_date) if paper.published_date else "Unknown"

                paper_prompt = active_paper_prompt_template.format(
                    paper_title=paper.title or "N/A",
                    paper_authors=paper_authors_str,
                    paper_publication_date=paper_pub_date_str,
                    paper_content_snippet=full_text_snippet,
                    target_audience=audience_type,
                    overall_instructions=paper_length_guidance
                )
                summary_tasks.append(self._generate_single_summary(
                    paper_prompt, paper_max_tokens_individual, paper_summary_semaphore
                ))
            
            paper_summaries_results = await asyncio.gather(*summary_tasks)

            for i, paper in enumerate(papers_to_summarize):
                title = paper.title or 'Untitled Paper'
                link_content = f"<a href='{paper.url}' target='_blank'>{title}</a>" if paper.url else title
                report_parts.append(f"<h3>{i+1}. {link_content}</h3>")
                
                if paper.authors:
                    report_parts.append(f"<p><em>Authors: {', '.join(paper.authors)}</em></p>")
                if paper.published_date:
                     report_parts.append(f"<p><em>Published: {self._format_date(paper.published_date)}</em></p>")
                
                summary_text = paper_summaries_results[i]
                report_parts.append(f"<h4>Key Insights and Analysis:</h4>")
                report_parts.append(self._text_to_html_paragraphs(summary_text))
                report_parts.append("<br>")
        else:
            if is_url_input:
                report_parts.append("<p>No similar papers were found or provided for summarization.</p>")
            else:
                report_parts.append("<p>No supporting papers were found or provided for summarization.</p>")

        return "\n".join(report_parts)

    def _text_to_html_paragraphs(self, text: str) -> str:
        if not text or not text.strip():
            return "<p><em>Summary could not be generated or is empty.</em></p>"
        
        # Replace single newlines that are not preceded/followed by another newline with a space (to join sentences)
        # Then split by double newlines (or more) to identify paragraphs
        processed_text = text.strip()
        # Heuristic: if a line is short and doesn't end with punctuation, it might be part of a list item or broken sentence.
        # This is tricky. For now, let's assume LLM provides reasonably paragraphed text or text where \n is a paragraph break.
        
        paragraphs = [p.strip() for p in processed_text.split('\n') if p.strip()] # Simpler split by any newline
        
        # Filter out any "empty" paragraphs again after stripping
        html_paragraphs = [f"<p>{p}</p>" for p in paragraphs if p]
        if not html_paragraphs: # If all paragraphs were empty after stripping
             return "<p><em>Summary content appears to be empty after formatting.</em></p>"
        return "\n".join(html_paragraphs)
    
    def _format_date(self, date_str: Optional[Union[str, datetime]]) -> Optional[str]:
        if not date_str:
            return None
        if isinstance(date_str, datetime):
            return date_str.strftime("%B %d, %Y")
        try:
            # Try parsing ISO format first (common from APIs)
            if 'T' in date_str:
                dt_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            # Try parsing YYYY-MM-DD
            elif len(date_str) == 10 and date_str.count('-') == 2:
                dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
            # Try parsing Month Day, Year (if it's already in that format)
            else:
                try:
                    dt_obj = datetime.strptime(date_str, "%B %d, %Y")
                except ValueError: # Fallback for other unhandled string formats
                    logger.warning(f"Unrecognized date string format: {date_str}. Returning as is.")
                    return date_str
            return dt_obj.strftime("%B %d, %Y")
        except ValueError:
            logger.warning(f"Could not parse date string: {date_str}. Returning as is.")
            return date_str
