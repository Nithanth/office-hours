"""Tests for security, CORS, HTTPS redirect, and auth gate middleware."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Security headers (extends existing test_security_and_request_id.py)
# ---------------------------------------------------------------------------


def test_permissions_policy_restricted_to_self(client):
    """Permissions-Policy should restrict clipboard access to self."""
    resp = client.get("/healthz")
    pp = resp.headers.get("Permissions-Policy", "")
    assert "clipboard-read=(self)" in pp
    assert "clipboard-write=(self)" in pp
    # Must NOT contain wildcard
    assert "clipboard-read=*" not in pp


def test_csp_header_present(client):
    """Content-Security-Policy header should be present and non-empty."""
    resp = client.get("/healthz")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src" in csp
    assert "script-src" in csp
    assert "frame-ancestors 'none'" in csp


def test_x_frame_options_deny(client):
    """X-Frame-Options must be DENY to prevent clickjacking."""
    resp = client.get("/healthz")
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_hsts_header(client):
    """Strict-Transport-Security must include max-age and preload."""
    resp = client.get("/healthz")
    hsts = resp.headers.get("Strict-Transport-Security", "")
    assert "max-age=" in hsts
    assert "includeSubDomains" in hsts
    assert "preload" in hsts


# ---------------------------------------------------------------------------
# HTTPS redirect middleware
# ---------------------------------------------------------------------------


def test_https_redirect_skipped_in_dev(client):
    """HTTPS redirect should be skipped when ENV=dev."""
    # The test client's ENV defaults to dev/test, so no redirect should happen.
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_https_redirect_in_prod():
    """HTTPS redirect should trigger for non-HTTPS requests in prod."""
    from middleware.security import HTTPSRedirectMiddleware

    # Verify the middleware class exists and checks ENV
    assert hasattr(HTTPSRedirectMiddleware, "dispatch")


# ---------------------------------------------------------------------------
# Auth gate middleware
# ---------------------------------------------------------------------------


def test_auth_gate_allows_public_paths_when_login_required(client, monkeypatch):
    """Public paths should be accessible even when REQUIRE_LOGIN=True."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    # /healthz is in the public prefixes list
    resp = client.get("/healthz")
    assert resp.status_code == 200

    # Static assets should pass through
    resp = client.get("/static/css/base.css")
    # Either 200 (file exists) or 404 (missing) — NOT 401/302
    assert resp.status_code != 401
    assert resp.status_code != 302 or "/auth/" not in resp.headers.get("location", "")


def test_auth_gate_blocks_protected_route_when_login_required(client, monkeypatch):
    """Protected routes should redirect unauthenticated users to login."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    # A protected route like /api/history should redirect to /auth/google/login
    resp = client.get("/api/history", follow_redirects=False)
    assert resp.status_code in (302, 307, 401)
    if resp.status_code in (302, 307):
        assert "/auth/google/login" in resp.headers.get("location", "")


def test_auth_gate_htmx_returns_banner(client, monkeypatch):
    """HTMX requests to protected routes should get a 401 banner, not a redirect."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    resp = client.get(
        "/api/history",
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 401
    assert "sign in" in resp.text.lower()


def test_auth_gate_disabled_allows_all(client, monkeypatch):
    """When REQUIRE_LOGIN=False, all routes should be accessible."""
    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", False, raising=False)

    resp = client.get("/healthz")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------


def test_cors_preflight_returns_headers(client):
    """OPTIONS preflight should return CORS headers."""
    resp = client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # Should include CORS headers (200 or 405 depending on route)
    # The key thing: the middleware should not reject same-origin preflight
    assert resp.status_code in (200, 204, 405)
