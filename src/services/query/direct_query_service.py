"""Service for answering direct research questions (Ask-LLM flow).

The public API is intentionally identical so the HTTP layer can
switch over easily.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from functools import partial
from typing import Any

from loguru import logger

from prompts.prompt_builder import (
    AUDIENCE_PROMPT_MAP,
    DEFAULT_SYSTEM_PROMPT,
    DIRECT_QUERY_USER_PREFIX,
)
from services.core.exa_client import ExaClient
from services.core.llm_service import LLMService
from services.core.llm_utils.types import ProviderName
from utils.html_builder import text_to_html_paragraphs

# ---------------------------------------------------------------------------
# Public service class
# ---------------------------------------------------------------------------


class DirectQueryService:
    """Stateless helper that generates an HTML answer for a direct query."""

    # Hard cap so the answer stays short even if caller forgets to bound it
    _HARD_CAP_TOKENS: int = 320

    def __init__(
        self,
        llm_service: LLMService,
        exa_client: ExaClient,
        selected_model_api_id: str,
        selected_model_provider: ProviderName,
    ) -> None:
        self.llm_service = llm_service
        self.exa_client = exa_client
        self.selected_model_api_id = selected_model_api_id
        self.selected_model_provider = selected_model_provider

    # ---------------------------------------------------------------------
    # Public façade
    # ---------------------------------------------------------------------

    async def generate(
        self,
        query: str,
        audience_type: str = "technical",
        num_supporting_papers: int = 3,
        max_tokens: int | None = None,
    ) -> str:
        """Return an HTML snippet answering *query* directly.

        The answer is grounded with Exa's Answer API where available; we embed
        the supporting passages below the answer.
        """

        # 1) Build system prompt tailored to audience --------------------------------
        aud_cfg = AUDIENCE_PROMPT_MAP.get(
            audience_type, AUDIENCE_PROMPT_MAP["technical"]
        )
        system_prompt = (
            DEFAULT_SYSTEM_PROMPT
            + " "
            + aud_cfg["system_suffix"]
            + " Respond with a direct answer in plain paragraphs. Do NOT output section headings, bullet points, or numbered lists."
        )

        # 2) Fetch grounding passages via Exa Answer API -----------------------------
        passages_block = await self._fetch_supporting_passages(
            query, num_supporting_papers
        )

        # 3) Build user prompt -------------------------------------------------------
        user_prompt = (
            DIRECT_QUERY_USER_PREFIX + "\n\nQuestion: " + query.strip() + passages_block
        )

        # 4) Call LLM ---------------------------------------------------------------
        max_tokens = (
            self._HARD_CAP_TOKENS
            if max_tokens is None
            else min(max_tokens, self._HARD_CAP_TOKENS)
        )
        start = time.time()
        result = await self.llm_service.generate_completion_safe(
            provider_name=self.selected_model_provider,
            model_name=self.selected_model_api_id,
            prompt=user_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=0.5,
        )
        logger.info("direct_query | llm_latency={:.2f}s", time.time() - start)

        # 5) Post-process -----------------------------------------------------------
        if not result["ok"]:
            logger.error("direct_query_failed", error=result["content"])
            return f"<div class='error-banner'>Error: {html.escape(result['content'])}</div>"

        cleaned = self._clean_markdown(result["content"].strip())
        html_body = text_to_html_paragraphs(cleaned)
        return f"<div id='oh-answer' class='direct-query-answer'>{html_body}</div>"

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    async def _fetch_supporting_passages(self, query: str, k: int) -> str:
        """Return formatted passage list (\n\nSOURCE PASSAGES: ... ) or '' on failure."""
        try:
            loop = asyncio.get_running_loop()
            answer: dict[str, Any] = await loop.run_in_executor(
                None, partial(self.exa_client.answer, query, top_k=k)
            )
        except Exception as exc:
            logger.warning("exa_answer_failed", error=str(exc))
            return ""

        if not answer or not answer.get("passages"):
            return ""

        lines: list[str] = []
        for idx, p in enumerate(answer["passages"], 1):
            snippet = p.get("snippet", "").replace("\n", " ").strip()
            url = p.get("url", "")
            lines.append(f"[{idx}] {snippet} (Source: {url})")
        return "\n\nSOURCE PASSAGES:\n" + "\n".join(lines)

    @staticmethod
    def _clean_markdown(text: str) -> str:
        """Remove bullet/italics markers crudely so the answer reads as prose."""
        # Remove leading list markers
        text = re.sub(r"^\s*([*\-]|\d+\.)\s+", "", text, flags=re.MULTILINE)
        # Strip **bold** and *italic* markers
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        return text.replace("*", "")

    # _text_to_html_paragraphs removed – use utils.html_builder


__all__: list[str] = ["DirectQueryService"]
