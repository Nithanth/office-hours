"""Open-redirect guard for the OAuth ``next`` parameter (Google + Notion)."""

from __future__ import annotations

import urllib.parse
from types import SimpleNamespace
from typing import Any

import pytest

from services.auth.session import safe_local_path

OFFSITE = [
    "https://evil.example",
    "https://evil.example/login",
    "//evil.example",
    "//evil.example/login",
    "/\\evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "javascript:alert(1)",
    "evil.example",
    "",
    None,
    123,
]


@pytest.mark.parametrize("target", OFFSITE)
def test_offsite_targets_fall_back_to_root(target: Any) -> None:
    assert safe_local_path(target) == "/"


@pytest.mark.parametrize(
    "target", ["/", "/library", "/reports?id=1&x=2", "/topic/foo#section", "/a//b"]
)
def test_local_paths_pass_through(target: str) -> None:
    assert safe_local_path(target) == target


@pytest.mark.parametrize("target", ["//evil.example", "/\\evil.example"])
async def test_google_login_signs_only_local_next(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    from api.routes import auth as auth_module

    signed: list[dict[str, Any]] = []
    monkeypatch.setattr(auth_module, "_auth_configured", lambda: True)
    monkeypatch.setattr(
        auth_module, "sign_oauth_state", lambda p: signed.append(p) or "state"
    )
    monkeypatch.setattr(auth_module, "set_oauth_state_cookie", lambda *a, **k: None)

    await auth_module.google_login(request=SimpleNamespace(), next=target)
    assert signed[0]["next"] == "/"


async def test_notion_connect_signs_only_local_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routes import integrations as integ

    signed: list[dict[str, Any]] = []
    monkeypatch.setattr(integ, "get_auth_user_id", lambda r: "user-1")
    monkeypatch.setattr(integ, "_notion_oauth_configured", lambda: True)
    monkeypatch.setattr(integ, "sign_oauth_state", lambda p: signed.append(p) or "s")
    monkeypatch.setattr(integ, "set_oauth_state_cookie", lambda *a, **k: None)

    await integ.notion_connect(request=SimpleNamespace(), next="https://evil.example")
    assert signed[0]["next"] == "/"


async def test_notion_connect_login_bounce_drops_offsite_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.routes import integrations as integ

    monkeypatch.setattr(integ, "get_auth_user_id", lambda r: None)
    resp = await integ.notion_connect(
        request=SimpleNamespace(), next="https://evil.example"
    )
    location = urllib.parse.unquote(resp.headers["location"])
    assert "evil.example" not in location


@pytest.mark.parametrize(
    ("state_next", "expected"),
    [
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/library", "/library"),
    ],
)
async def test_notion_callback_redirects_only_locally(
    monkeypatch: pytest.MonkeyPatch, state_next: str, expected: str
) -> None:
    from api.routes import integrations as integ
    from services.persistence import repositories

    monkeypatch.setattr(integ, "require_auth_user_id", lambda r: "user-1")
    monkeypatch.setattr(integ, "_notion_oauth_configured", lambda: True)
    monkeypatch.setattr(
        integ,
        "verify_oauth_state",
        lambda s: {"next": state_next, "provider": "notion"},
    )

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"access_token": "tok"}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def post(self, *a: Any, **k: Any) -> _Resp:
            return _Resp()

    async def _session_scope() -> Any:
        yield object()

    async def _upsert(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(integ.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(integ, "session_scope", _session_scope)
    monkeypatch.setattr(repositories, "upsert_notion_integration", _upsert)

    request = SimpleNamespace(cookies={integ.OAUTH_STATE_COOKIE_NAME: "s"})
    resp = await integ.notion_callback(request=request, code="c", state="s")
    assert resp.headers["location"] == expected
