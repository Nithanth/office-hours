"""Middleware behaviour: security headers + X-Request-Id propagation."""

from __future__ import annotations

from uuid import uuid4


def test_security_headers_and_request_id(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200

    # Security headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=")

    # Request ID must be auto-generated
    req_id = resp.headers.get("X-Request-Id")
    assert req_id, "middleware should inject X-Request-Id"

    # When client supplies header, it should be echoed back unchanged
    custom_id = str(uuid4())
    resp2 = client.get("/healthz", headers={"X-Request-Id": custom_id})
    assert resp2.headers["X-Request-Id"] == custom_id
