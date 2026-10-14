"""Regression tests for the prod bug-squash:

- ``/report/stream`` must claim its request token on first connect so a
  post-``done`` EventSource reconnect can't re-run generation and persist a
  duplicate report.
- ``_paper_pdf_links`` must skip legal/boilerplate PDFs (e.g. exa.ai's
  ``/assets/Exa_Labs_Terms_of_Service.pdf``) so extraction never ingests a
  site's terms of service as the paper's text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.routers import report_stream
from services.paper.text_extractor import _paper_pdf_links


@pytest.mark.asyncio
async def test_report_stream_claims_token_on_first_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_stream, "_REPORT_REQUEST_DIR", tmp_path)
    report_stream.REPORT_REQUEST_CACHE.clear()

    payload = {
        "original_query": "transformers",
        "selected_papers_json": [],
        "audience_type": "technical",
        "summary_size": "M",
        "selected_model_key": "model",
        "is_url_input": False,
    }
    report_stream._save_report_request("tok", payload)
    report_stream.REPORT_REQUEST_CACHE.clear()

    async def _fake_session(_request: object) -> str:
        return "sess-1"

    async def _fake_services() -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(report_stream, "get_session_id", _fake_session)
    monkeypatch.setattr(report_stream, "_get_services", _fake_services)

    # First connect consumes the token even though generation can't proceed
    # (empty paper list → error response) — the claim happens first.
    await report_stream.report_stream(request=None, token="tok")  # type: ignore[arg-type]
    assert report_stream._load_report_request("tok") is None

    # A reconnect (post-done or mid-run) must not re-run generation — the
    # claimed token returns a terminal SSE (error block + done) so the
    # client's reconnect loop stops instead of 204-looping forever.
    resp = await report_stream.report_stream(request=None, token="tok")  # type: ignore[arg-type]
    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"


def test_paper_pdf_links_skips_legal_boilerplate() -> None:
    raw_html = """
    <html><body>
      <a href="/assets/Exa_Labs_Terms_of_Service.pdf">Terms</a>
      <a href="/assets/privacy-policy.pdf">Privacy</a>
      <a href="https://arxiv.org/pdf/2411.00099.pdf">Download PDF</a>
      <a href="/static/site-disclaimer.pdf">Disclaimer</a>
    </body></html>
    """
    links = _paper_pdf_links(raw_html)
    assert links == ["https://arxiv.org/pdf/2411.00099.pdf"]


def test_paper_pdf_links_empty_when_only_boilerplate() -> None:
    raw_html = '<a href="/legal/terms.pdf">Terms</a><a href="/imprint.pdf">Imprint</a>'
    assert _paper_pdf_links(raw_html) == []
