"""Integration tests for the report-notes generation endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.parametrize(
    "output_format,expected_ct",
    [
        ("txt", "text/plain"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
)
def test_generate_report_notes(client, output_format: str, expected_ct: str):
    resp = client.post(
        "/generate-report-notes",
        data={"report_text": "Some report text", "output_format": output_format},
    )
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith(expected_ct)

    dispo = resp.headers.get("Content-Disposition", "")
    assert "attachment;" in dispo
    if output_format == "txt":
        assert resp.text == "stub notes"
    else:
        assert resp.content.startswith(b"docx-bytes")
