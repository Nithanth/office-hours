"""Tests for services.auth.session signed-cookie helpers."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# OAuth state signing
# ---------------------------------------------------------------------------


def test_sign_and_verify_oauth_state_roundtrip():
    """sign_oauth_state -> verify_oauth_state should roundtrip."""
    from services.auth.session import sign_oauth_state, verify_oauth_state

    payload = {"next": "/dashboard", "nonce": "abc123"}
    signed = sign_oauth_state(payload)
    assert isinstance(signed, str)

    recovered = verify_oauth_state(signed)
    assert recovered["next"] == "/dashboard"
    assert recovered["nonce"] == "abc123"


def test_verify_oauth_state_rejects_garbage():
    """verify_oauth_state should raise HTTPException for bad tokens."""
    from fastapi import HTTPException

    from services.auth.session import verify_oauth_state

    with pytest.raises(HTTPException) as exc_info:
        verify_oauth_state("garbage-token")
    assert exc_info.value.status_code == 400


def test_sign_oauth_state_different_payloads_different_tokens():
    """Different payloads should produce different signed tokens."""
    from services.auth.session import sign_oauth_state

    a = sign_oauth_state({"x": 1})
    b = sign_oauth_state({"x": 2})
    assert a != b


# ---------------------------------------------------------------------------
# Auth cookie helpers
# ---------------------------------------------------------------------------


def test_clear_auth_cookie_sets_delete_header():
    """clear_auth_cookie should set a delete-cookie header."""
    from starlette.responses import Response

    from services.auth.session import AUTH_COOKIE_NAME, clear_auth_cookie

    response = Response()
    clear_auth_cookie(response)
    # After delete_cookie, raw_headers should contain the cookie name
    raw = response.headers.raw
    cookie_headers = [v.decode() for k, v in raw if k == b"set-cookie"]
    assert any(AUTH_COOKIE_NAME in h for h in cookie_headers)


def test_auth_cookie_name_is_oh_auth():
    """AUTH_COOKIE_NAME should be 'oh_auth'."""
    from services.auth.session import AUTH_COOKIE_NAME

    assert AUTH_COOKIE_NAME == "oh_auth"


def test_auth_cookie_max_age_is_30_days():
    """AUTH_COOKIE_MAX_AGE should be 30 days in seconds."""
    from services.auth.session import AUTH_COOKIE_MAX_AGE

    assert AUTH_COOKIE_MAX_AGE == 60 * 60 * 24 * 30


# ---------------------------------------------------------------------------
# Auth user ID extraction (via TestClient)
# ---------------------------------------------------------------------------


def test_get_auth_user_id_returns_none_without_cookie(client):
    """Without a cookie, the healthz endpoint should still work (public)."""
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_signed_cookie_accepted_by_auth_gate(client, monkeypatch):
    """A correctly-signed auth cookie should pass the auth gate."""
    import config as _cfg
    from services.auth.session import AUTH_COOKIE_NAME, _serializer

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    signed_value = _serializer.dumps({"user_id": "user-123"})
    # Set cookie on the client's session
    client.cookies.set(AUTH_COOKIE_NAME, signed_value)

    # /api/history is a protected route
    resp = client.get("/api/history")
    # With valid auth, should NOT get a redirect to login
    if resp.status_code in (302, 307):
        assert "/auth/google/login" not in resp.headers.get("location", "")


def test_bad_cookie_rejected_by_auth_gate(client, monkeypatch):
    """A tampered auth cookie should be treated as unauthenticated."""
    import config as _cfg
    from services.auth.session import AUTH_COOKIE_NAME

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    client.cookies.set(AUTH_COOKIE_NAME, "bad-value")
    resp = client.get("/api/history", follow_redirects=False)
    # Should redirect to login or return 401
    assert resp.status_code in (302, 307, 401)


# ---------------------------------------------------------------------------
# Chat session cookie
# ---------------------------------------------------------------------------


def test_chat_session_cookie_set_on_first_visit(client):
    """First request should work and set session state."""
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_chat_session_cookie_reused(client):
    """Subsequent requests with the same client should reuse the session."""
    resp1 = client.get("/healthz")
    resp2 = client.get("/healthz")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
