"""Unit tests for the OAuth callback's state-cookie comparison logic.

The state token is signed, but a signed state alone doesn't prevent login
CSRF: an attacker can start a flow and hand the victim their own callback
link. The cookie binds the flow to the initiating browser, so the callback
must always require it to match, on every scheme (behind Railway's TLS proxy
request.url.scheme is http).
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
async def test_http_rejects_when_cookie_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP + no cookie → 400. Prod sees http behind the TLS proxy, so a
    scheme-based fallback would reopen login CSRF."""

    with pytest.raises(HTTPException) as excinfo:
        await _call_callback(monkeypatch, scheme="http", cookie_value=None, state_value="state-abc")
    assert excinfo.value.status_code == 400
    assert "state mismatch" in excinfo.value.detail.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_matching_cookie_passes_state_check(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    """Cookie == state → we get past the check and reach the (mocked) network."""

    with pytest.raises(Exception) as excinfo:
        await _call_callback(
            monkeypatch, scheme=scheme, cookie_value="state-abc", state_value="state-abc"
        )
    assert not isinstance(excinfo.value, HTTPException)
    assert "post() called" in str(excinfo.value)


@pytest.mark.asyncio
async def test_non_ascii_state_rejected_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        await _call_callback(
            monkeypatch, scheme="https", cookie_value="state-abc", state_value="stäte"
        )
    assert excinfo.value.status_code == 400


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
