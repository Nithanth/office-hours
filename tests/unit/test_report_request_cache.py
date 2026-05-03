from __future__ import annotations

from pathlib import Path

import pytest

from api.routers import report_stream


def test_report_request_cache_round_trips_to_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report_stream, "_REPORT_REQUEST_DIR", tmp_path)
    report_stream.REPORT_REQUEST_CACHE.clear()

    payload = {
        "original_query": "transformers",
        "selected_papers_json": ["{}"],
        "audience_type": "technical",
        "summary_size": "M",
        "selected_model_key": "model",
        "is_url_input": False,
    }

    report_stream._save_report_request("token-1", payload)
    report_stream.REPORT_REQUEST_CACHE.clear()

    assert report_stream._load_report_request("token-1") == payload

    report_stream._delete_report_request("token-1")
    report_stream.REPORT_REQUEST_CACHE.clear()

    assert report_stream._load_report_request("token-1") is None
