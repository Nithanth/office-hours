"""Unit-test a few sizing / helper methods on ReportGenerator without hitting LLM."""

from __future__ import annotations

from services.report.report_generator import ReportGenerator


class _DummyLLM:  # minimal stub
    async def generate_completion_safe(self, *a, **k):  # noqa: D401
        return {"ok": True, "content": "stub"}


class _DummyExa:  # noqa: D101 – stub
    pass


def _make_rg():
    return ReportGenerator(
        llm_service=_DummyLLM(),
        exa_client=_DummyExa(),
        selected_model_api_id="models/gpt",
        selected_model_provider="openai",
        selected_model_input_token_limit=8192,
    )


def test_output_token_target():
    rg = _make_rg()
    # Should pull from config map
    tok = rg._get_output_token_target("technical", "M", False)
    assert isinstance(tok, int) and tok > 0


def test_prompt_overhead_estimate():
    rg = _make_rg()
    overhead = rg._estimate_prompt_overhead("technical", False)
    assert overhead > 0


def test_content_snippet_chars():
    rg = _make_rg()
    chars = rg._get_max_content_snippet_chars("technical", "S", False)
    # Should be less than model limit * AVG chars per token (rough check)
    assert chars < rg.selected_model_input_token_limit * rg.AVG_CHARS_PER_TOKEN
