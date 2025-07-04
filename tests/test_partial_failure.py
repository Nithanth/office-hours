import json
from typing import Any

from fastapi.testclient import TestClient
from routers import report_stream as rs

from config import config as app_config
from main import app
from services.report_generator import ReportGenerator


def _make_paper(id_: str, title: str) -> dict[str, str]:
    return {
        "id": id_,
        "title": title,
        "url": "https://example.com/" + id_,
        "text_content": "dummy",
    }


async def _stub_generate(
    self: Any,
    prompt: str,
    max_tokens: int,
    semaphore: Any | None = None,
    system_prompt: str | None = None,
    cache_key: str | None = None,
) -> str:  # noqa: D401,E501
    """Async stub that raises for prompts containing marker phrase."""
    if "Fail Paper" in prompt:
        raise RuntimeError("forced failure for test")
    return "OK summary"


def test_partial_failure_event(monkeypatch: Any) -> None:
    """The /report/stream endpoint should emit a `failures` event with titles."""

    # Monkey-patch the LLM call so we control failures.
    monkeypatch.setattr(ReportGenerator, "_generate_single_summary", _stub_generate, raising=True)

    # Ensure the streaming endpoint does not error out on missing services.
    async def _dummy_get_services() -> tuple[Any, Any]:  # noqa: D401
        class _Dummy:
            pass

        return _Dummy(), _Dummy()

    monkeypatch.setattr(rs, "_get_services", _dummy_get_services, raising=True)

    # Skip the 60-second deferred cache deletion to avoid hanging the stream.
    async def _noop_del_cache(token: str, delay: int = 60) -> None:  # noqa: D401
        pass

    monkeypatch.setattr(rs, "_del_cache_later", _noop_del_cache, raising=True)

    client = TestClient(app)

    good = _make_paper("good1", "Good Paper")
    bad = _make_paper("bad1", "Fail Paper")  # will trigger stub error

    # 1. Kick off report generation (POST)
    resp = client.post(
        "/generate-report-incremental",
        data={
            "original_query_for_report": "unit-test",
            "selected_papers_json": [json.dumps(good), json.dumps(bad)],
            "audience_type": "technical",
            "summary_size": "S",
            "selected_model_key": app_config.DEFAULT_MODEL_KEY,
            "is_url_input": "true",
        },
    )
    assert resp.status_code == 200

    # 2. Obtain token that the router stored
    assert rs.REPORT_REQUEST_CACHE, "Token not cached"
    token = next(iter(rs.REPORT_REQUEST_CACHE))

    # 3. Consume streaming endpoint (concatenated response)
    stream_resp = client.get(f"/report/stream?token={token}")
    assert stream_resp.status_code == 200
    body = stream_resp.text

    # The failures event and the failing paper title should be present
    assert "event: failures" in body
    assert "Fail Paper" in body

    # Ensure the stream finished successfully by emitting the done event
    assert "event: done" in body

    # Optional: verbose success output when running with -s
    print("✅ Partial failure path handled; done event received.")
