"""Unit tests for the OAuth callback's state-cookie comparison logic.

The state token itself is cryptographically signed (URLSafeTimedSerializer
with OH_SESSION_SECRET), so an attacker can't forge it. The cookie
comparison is defense-in-depth CSRF binding. On localhost over plain HTTP,
browsers (especially privacy-strict ones) sometimes drop the SameSite=Lax
state cookie across the Google redirect — producing 400 on every dev sign-in.

The route now:
- HTTPS: strict comparison (cookie required, must match state).
- HTTP + cookie missing: warn and fall through to signature verification.
- HTTP + cookie present but != state: still rejected (real tampering signal).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException


async def _call_callback(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scheme: str,
    cookie_value: str | None,
    state_value: str,
) -> Any:
    """Drive ``google_callback`` with a fake Request and short-circuit any
    side-effects (HTTP, DB) so the test focuses only on the cookie branch."""

    from api.routes import auth as auth_module

    # Pass the configured check.
    monkeypatch.setattr(auth_module, "_auth_configured", lambda: True)

    # Avoid signature validation: real signer needs a secret we don't want
    # to wire up for this unit test. Returning a dict means downstream
    # code reads ``next`` safely.
    monkeypatch.setattr(auth_module, "verify_oauth_state", lambda s: {"next": "/"})

    # Block the network. If the cookie-check happens to let the request
    # through, raise a sentinel so the test fails loudly rather than
    # silently making a real Google call.
    class _NetworkBlocked(Exception):
        pass

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            raise _NetworkBlocked("post() called")

        async def get(self, *args: Any, **kwargs: Any) -> Any:
            raise _NetworkBlocked("get() called")

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _FakeClient)

    request = SimpleNamespace(
        cookies={"oh_oauth_state": cookie_value} if cookie_value is not None else {},
        url=SimpleNamespace(scheme=scheme, hostname="localhost"),
    )

    return (
        await auth_module.google_callback(
            request=request,
            code="fake-code",
            state=state_value,
        ),
        _NetworkBlocked,
    )


@pytest.mark.asyncio
async def test_https_strict_when_cookie_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS + no cookie → 400. Production must not be loosened."""

    with pytest.raises(HTTPException) as excinfo:
        await _call_callback(
            monkeypatch, scheme="https", cookie_value=None, state_value="state-abc"
        )
    assert excinfo.value.status_code == 400
    assert "state mismatch" in excinfo.value.detail.lower()


@pytest.mark.asyncio
async def test_http_falls_through_when_cookie_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP + no cookie → state check passes; we should reach the network
    layer (which is mocked to raise the sentinel)."""

    with pytest.raises(Exception) as excinfo:
        result, NetworkBlocked = await _call_callback(
            monkeypatch, scheme="http", cookie_value=None, state_value="state-abc"
        )
    # The sentinel must be the _NetworkBlocked exception (class created
    # inside _call_callback) — i.e. we got past the cookie check.
    assert "NetworkBlocked" in type(excinfo.value).__name__ or "post() called" in str(
        excinfo.value
    )


@pytest.mark.asyncio
async def test_http_rejects_when_cookie_present_but_mismatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP + cookie present but != state → real tampering signal, still 400."""

    with pytest.raises(HTTPException) as excinfo:
        await _call_callback(
            monkeypatch,
            scheme="http",
            cookie_value="something-else",
            state_value="state-abc",
        )
    assert excinfo.value.status_code == 400
    assert "state mismatch" in excinfo.value.detail.lower()


@pytest.mark.asyncio
async def test_https_rejects_when_cookie_present_but_mismatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTPS + cookie present but != state → 400 (unchanged behavior)."""

    with pytest.raises(HTTPException) as excinfo:
        await _call_callback(
            monkeypatch,
            scheme="https",
            cookie_value="something-else",
            state_value="state-abc",
        )
    assert excinfo.value.status_code == 400
    assert "state mismatch" in excinfo.value.detail.lower()


@pytest.mark.asyncio
async def test_missing_code_or_state_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: the existing guard against missing OAuth params is intact."""

    from api.routes import auth as auth_module

    monkeypatch.setattr(auth_module, "_auth_configured", lambda: True)

    request = SimpleNamespace(
        cookies={"oh_oauth_state": "state-abc"},
        url=SimpleNamespace(scheme="http", hostname="localhost"),
    )

    with pytest.raises(HTTPException) as excinfo:
        await auth_module.google_callback(
            request=request,
            code=None,
            state="state-abc",
        )
    assert excinfo.value.status_code == 400
    assert "missing" in excinfo.value.detail.lower()
