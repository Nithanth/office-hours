"""Integration tests for the /generate-blog flow.

Covers the new two-step blog pipeline:
  1. POST /generate-blog        – queues an in-memory generation, returns token
  2. GET  /blog-status?token=…  – polled until state=="ready"
  3. GET  /blog-view?token=…    – serves the rendering shell
  4. GET  /blog-view stays alive across renders (no pop-on-view 404).
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _wait_for_ready(
    client: TestClient, token: str, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Poll /blog-status until ready/error/expired or timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get("/blog-status", params={"token": token})
        assert resp.status_code in (200, 404), resp.status_code
        body: dict[str, Any] = resp.json()
        state = body.get("state")
        if state in {"ready", "error", "expired"}:
            return body
        time.sleep(0.05)
    pytest.fail(f"/blog-status did not settle within {timeout}s")
    raise AssertionError("unreachable")  # for mypy


def test_generate_blog_returns_token_and_url(client: TestClient) -> None:
    resp = client.post(
        "/generate-blog", data={"report_text": "A real research report."}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and body["token"]
    assert body.get("url") == f"/blog-view?token={body['token']}"


def test_generate_blog_rejects_empty_report(client: TestClient) -> None:
    resp = client.post("/generate-blog", data={"report_text": "   "})
    assert resp.status_code == 400


def test_blog_status_eventually_returns_markdown(client: TestClient) -> None:
    resp = client.post(
        "/generate-blog", data={"report_text": "A real research report."}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    body = _wait_for_ready(client, token)
    assert body["state"] == "ready", body
    assert isinstance(body.get("markdown"), str)
    assert body["markdown"]


def test_blog_status_404_for_unknown_token(client: TestClient) -> None:
    resp = client.get("/blog-status", params={"token": "definitely-not-a-real-token"})
    assert resp.status_code == 404
    assert resp.json().get("state") == "expired"


def test_blog_view_does_not_pop_cache(client: TestClient) -> None:
    """Refreshing /blog-view must keep working (no one-shot cache)."""

    resp = client.post("/generate-blog", data={"report_text": "Another report."})
    token = resp.json()["token"]
    _wait_for_ready(client, token)

    first = client.get("/blog-view", params={"token": token})
    second = client.get("/blog-view", params={"token": token})
    assert first.status_code == 200
    assert second.status_code == 200
    assert "blog-view.js" in first.text
    assert "blog-view.js" in second.text


def test_blog_view_404_for_unknown_token(client: TestClient) -> None:
    resp = client.get("/blog-view", params={"token": "nope"})
    assert resp.status_code == 404


def test_generate_blog_accepts_customization_options(client: TestClient) -> None:
    """POST /generate-blog accepts audience/length/tone/extra without erroring."""

    resp = client.post(
        "/generate-blog",
        data={
            "report_text": "A real research report.",
            "audience": "layperson",
            "length": "short",
            "tone": "casual",
            "extra": "Emphasise reproducibility.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body and body["token"]
    # Generation must still complete cleanly with the customized prompt.
    body_status = _wait_for_ready(client, body["token"])
    assert body_status["state"] == "ready", body_status


def test_generate_blog_tolerates_unknown_option_values(client: TestClient) -> None:
    """Unknown values fall back to defaults silently — no 4xx/5xx."""

    resp = client.post(
        "/generate-blog",
        data={
            "report_text": "Report.",
            "audience": "space-alien",  # not in the option dict
            "length": "enormous",
            "tone": "haiku",
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /regenerate-blog endpoint (Fix #6: revise with feedback)
# ---------------------------------------------------------------------------


def test_regenerate_blog_rejects_unknown_token(client: TestClient) -> None:
    resp = client.post(
        "/regenerate-blog",
        data={"token": "does-not-exist", "extra": "make it shorter"},
    )
    assert resp.status_code == 404


def test_regenerate_blog_rejects_empty_feedback(client: TestClient) -> None:
    """A regeneration without feedback is meaningless — must reject."""

    # First produce a real cache entry.
    orig = client.post("/generate-blog", data={"report_text": "Source report."})
    token = orig.json()["token"]
    _wait_for_ready(client, token)

    resp = client.post(
        "/regenerate-blog",
        data={"token": token, "extra": "   "},
    )
    assert resp.status_code == 400


def test_regenerate_blog_rejects_in_flight_token(client: TestClient) -> None:
    """Trying to regenerate while the original is still being written → 409."""
    from api.routers.report_notes import _BLOG_CACHE, _BlogEntry

    # Manually drop a pending entry into the cache so we don't have to race
    # the LLM. report_text is set so the "no source" check passes.
    _BLOG_CACHE["pending-tok"] = _BlogEntry(state="pending", report_text="src")
    try:
        resp = client.post(
            "/regenerate-blog",
            data={"token": "pending-tok", "extra": "shorter please"},
        )
        assert resp.status_code == 409
    finally:
        _BLOG_CACHE.pop("pending-tok", None)


def test_regenerate_blog_happy_path_returns_new_token(client: TestClient) -> None:
    """Successful regen returns a fresh token + URL, leaves original intact."""

    orig = client.post(
        "/generate-blog",
        data={
            "report_text": "Source report for revision.",
            "audience": "technical",
            "length": "medium",
            "tone": "distill",
        },
    )
    assert orig.status_code == 200
    orig_token = orig.json()["token"]
    _wait_for_ready(client, orig_token)

    resp = client.post(
        "/regenerate-blog",
        data={
            "token": orig_token,
            "audience": "layperson",
            "length": "short",
            "tone": "casual",
            "extra": "Make it more accessible to a non-technical reader, please.",
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["token"] != orig_token
    assert body["url"] == f"/blog-view?token={body['token']}"

    # The original entry should still exist (regenerate does not pop).
    from api.routers.report_notes import _BLOG_CACHE

    assert orig_token in _BLOG_CACHE, "original cache entry should be preserved"

    # The new entry should complete.
    new_body = _wait_for_ready(client, body["token"])
    assert new_body["state"] == "ready"


def test_regenerate_truncates_oversize_feedback(client: TestClient) -> None:
    """Feedback over 1000 chars is silently truncated, not rejected."""

    orig = client.post("/generate-blog", data={"report_text": "Src."})
    token = orig.json()["token"]
    _wait_for_ready(client, token)

    resp = client.post(
        "/regenerate-blog",
        data={"token": token, "extra": "A" * 2000},
    )
    # Truncated, not rejected.
    assert resp.status_code == 200
    new_body = _wait_for_ready(client, resp.json()["token"])
    assert new_body["state"] == "ready"


def test_blog_view_includes_revise_button(client: TestClient) -> None:
    """Blog-view shell renders the Revise button + loads blog-modal.js."""

    resp = client.post("/generate-blog", data={"report_text": "Src."})
    token = resp.json()["token"]
    _wait_for_ready(client, token)

    page = client.get("/blog-view", params={"token": token}).text
    assert 'id="blog-revise-btn"' in page
    assert "blog-modal.js" in page


def test_blog_view_includes_writing_pill_and_disabled_buttons(
    client: TestClient,
) -> None:
    """Blog page shell includes the 'Writing blog…' pill + initially disabled actions."""

    resp = client.post("/generate-blog", data={"report_text": "Src."})
    token = resp.json()["token"]
    page = client.get("/blog-view", params={"token": token}).text
    assert "blog-writing-pill" in page
    assert 'id="blog-save-html-btn"' in page
    assert "disabled" in page


# ---------------------------------------------------------------------------
# Persisted blog (report.blog_markdown) + in-page pane endpoints
# ---------------------------------------------------------------------------


def test_generate_blog_accepts_report_id(client: TestClient) -> None:
    """POST /generate-blog with report_id still completes (anonymous → no persist)."""

    resp = client.post(
        "/generate-blog",
        data={"report_text": "A real research report.", "report_id": "r-1"},
    )
    assert resp.status_code == 200
    body = _wait_for_ready(client, resp.json()["token"])
    assert body["state"] == "ready"


def test_report_blog_anonymous_session(client: TestClient) -> None:
    """GET /reports/{id}/blog works off the owner key, not auth.

    Anonymous sessions own reports too, so the request passes the owner
    check and fails on the unconfigured DB instead of 401ing.
    """

    resp = client.get("/reports/some-report-id/blog")
    assert resp.status_code == 503


def test_regenerate_blog_report_id_requires_auth(client: TestClient) -> None:
    """The report_id revision path needs an authed owner; anonymous falls
    through to the token lookup and 404s."""

    resp = client.post(
        "/regenerate-blog",
        data={"report_id": "r-1", "extra": "make it shorter"},
    )
    assert resp.status_code == 404


def test_reports_page_anonymous_not_auth_gated(client: TestClient) -> None:
    """GET /reports is owner-keyed, not auth-gated.

    Anonymous sessions list their own reports, so the request reaches the
    database check (503 with no DB configured) rather than 401ing.
    """

    resp = client.get("/reports")
    assert resp.status_code == 503


def test_reports_api_anonymous_not_auth_gated(client: TestClient) -> None:
    """GET /api/reports is owner-keyed: anonymous gets an empty-but-ok
    payload shape error from the missing DB, not a 401."""

    resp = client.get("/api/reports")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
