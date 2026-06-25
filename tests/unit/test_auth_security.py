"""Unit tests for auth security hardening."""

from __future__ import annotations

import importlib

import pytest


class TestSessionSecretValidation:
    """Tests for session secret enforcement at startup."""

    def test_prod_with_default_secret_exits(self, monkeypatch):
        """Production startup with insecure default should sys.exit(1)."""
        monkeypatch.setenv("ENV", "prod")
        monkeypatch.setenv("LUX_SESSION_SECRET", "dev-insecure-secret")
        import config

        with pytest.raises(SystemExit) as exc_info:
            importlib.reload(config)
        assert exc_info.value.code == 1

    def test_prod_with_strong_secret_ok(self, monkeypatch):
        """Production startup with custom secret should not exit."""
        monkeypatch.setenv("ENV", "prod")
        monkeypatch.setenv("LUX_SESSION_SECRET", "my-strong-secret-value-here")
        import config

        importlib.reload(config)
        assert config.config.LUX_SESSION_SECRET == "my-strong-secret-value-here"

    def test_dev_with_default_secret_warns(self, monkeypatch):
        """Dev mode with insecure default should emit a warning."""
        monkeypatch.setenv("ENV", "dev")
        monkeypatch.setenv("LUX_SESSION_SECRET", "dev-insecure-secret")
        import config

        with pytest.warns(UserWarning, match="insecure default"):
            importlib.reload(config)


class TestCookieSecurity:
    """Tests for cookie secure flag behavior."""

    def test_is_secure_cookie_prod_always_true(self):
        """In prod, cookies should always have Secure flag."""
        from unittest.mock import MagicMock

        import config as _cfg
        import services.auth.session as sess_mod

        request = MagicMock()
        request.url.scheme = "http"  # Even over HTTP in prod

        original_env = _cfg.config.ENV
        try:
            _cfg.config.ENV = "prod"
            assert sess_mod._is_secure_cookie(request) is True
        finally:
            _cfg.config.ENV = original_env

    def test_is_secure_cookie_dev_follows_scheme(self):
        """In dev, cookie Secure flag follows the request scheme."""
        from unittest.mock import MagicMock

        import config as _cfg
        import services.auth.session as sess_mod

        request = MagicMock()
        original_env = _cfg.config.ENV
        try:
            _cfg.config.ENV = "dev"
            request.url.scheme = "http"
            assert sess_mod._is_secure_cookie(request) is False
            request.url.scheme = "https"
            assert sess_mod._is_secure_cookie(request) is True
        finally:
            _cfg.config.ENV = original_env


class TestRateLimitHandler:
    """Tests for the custom rate limit handler."""

    def test_parse_retry_seconds_minute(self):
        from utils.rate_limit import _parse_retry_seconds

        assert _parse_retry_seconds("100 per 5 minute") == 300

    def test_parse_retry_seconds_hour(self):
        from utils.rate_limit import _parse_retry_seconds

        assert _parse_retry_seconds("10 per 1 hour") == 3600

    def test_parse_retry_seconds_default(self):
        from utils.rate_limit import _parse_retry_seconds

        assert _parse_retry_seconds("unknown format") == 60

    @staticmethod
    def _make_rate_limit_exc(detail: str = "100 per 5 minute"):
        """Create a RateLimitExceeded with mocked internals."""
        from unittest.mock import MagicMock

        exc = MagicMock(spec=Exception)
        exc.detail = detail
        return exc

    def test_htmx_request_gets_html(self):
        """HTMX requests should receive an HTML error banner."""
        from unittest.mock import MagicMock

        from utils.rate_limit import _rate_limit_exceeded_handler

        request = MagicMock()
        headers_mock = MagicMock()
        headers_mock.get = MagicMock(side_effect=lambda k, d=None: "true" if k == "HX-Request" else d)
        request.headers = headers_mock

        exc = self._make_rate_limit_exc("100 per 5 minute")
        resp = _rate_limit_exceeded_handler(request, exc)

        assert resp.status_code == 429
        assert "text/html" in resp.media_type
        assert "error-banner" in resp.body.decode()

    def test_json_request_gets_json(self):
        """Non-HTMX requests should receive JSON error."""
        from unittest.mock import MagicMock

        from utils.rate_limit import _rate_limit_exceeded_handler

        request = MagicMock()
        headers_mock = MagicMock()
        headers_mock.get = MagicMock(return_value=None)
        request.headers = headers_mock

        exc = self._make_rate_limit_exc("100 per 5 minute")
        resp = _rate_limit_exceeded_handler(request, exc)

        assert resp.status_code == 429
        assert "application/json" in resp.media_type
        body = resp.body.decode()
        assert "retry_after" in body
