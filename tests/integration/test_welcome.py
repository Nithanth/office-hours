"""Integration tests for the unauthenticated welcome / sign-in landing page.

The welcome card replaces the home page only when ``REQUIRE_LOGIN=1`` AND
the visitor has no auth cookie. The default test client (REQUIRE_LOGIN=False)
should always see the regular home page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_home_renders_regular_page_when_require_login_false(client: TestClient) -> None:
    """Default conftest state: REQUIRE_LOGIN=False → unauthenticated user
    gets the full home page, not the welcome card."""

    resp = client.get("/")
    assert resp.status_code == 200
    assert "welcome-card" not in resp.text
    # The home page renders a search form; the welcome page doesn't.
    assert "search_query" in resp.text


def test_home_renders_welcome_card_when_require_login_true_and_unauthenticated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQUIRE_LOGIN=True + no auth cookie → welcome card replaces home."""

    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    resp = client.get("/")
    assert resp.status_code == 200, resp.text[:200]
    # Welcome marker classes present.
    assert "welcome-card" in resp.text
    assert "welcome-cta" in resp.text
    # Sign-in CTA points to the existing OAuth endpoint with a next param.
    assert "/auth/google/login?next=" in resp.text
    # The full search UI is NOT shipped.
    assert "search_query" not in resp.text


def test_welcome_card_links_to_google_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Continue-with-Google button must be a real anchor to /auth/google/login."""

    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    resp = client.get("/")
    assert "Continue with Google" in resp.text
    assert 'href="/auth/google/login?next=' in resp.text


def test_welcome_loads_welcome_css(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dedicated stylesheet must be referenced so the card renders properly."""

    import config as _cfg

    monkeypatch.setattr(_cfg.config, "REQUIRE_LOGIN", True, raising=False)

    resp = client.get("/")
    assert "/static/css/welcome.css" in resp.text


def test_welcome_static_css_served(client: TestClient) -> None:
    """The welcome.css file must be reachable via the static mount."""

    resp = client.get("/static/css/welcome.css")
    assert resp.status_code == 200
    assert "welcome-card" in resp.text
