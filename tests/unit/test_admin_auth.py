from __future__ import annotations

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

import config as config_module
from api.routes.admin import _require_admin


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.mark.parametrize("env", ["prod", "dev", "test", "staging"])
def test_admin_fails_closed_without_key(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setattr(config_module.config, "ENV", env)
    monkeypatch.setattr(config_module.config, "ADMIN_API_KEY", None)

    with pytest.raises(HTTPException) as exc_info:
        _require_admin(None)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.parametrize("creds", [None, _bearer("wrong"), _bearer("")])
def test_admin_rejects_bad_credentials(
    monkeypatch: pytest.MonkeyPatch, creds: HTTPAuthorizationCredentials | None
) -> None:
    monkeypatch.setattr(config_module.config, "ADMIN_API_KEY", "s3cret")

    with pytest.raises(HTTPException) as exc_info:
        _require_admin(creds)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_admin_accepts_matching_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.config, "ADMIN_API_KEY", "s3cret")

    _require_admin(_bearer("s3cret"))


def test_flush_cache_endpoint_fails_closed_by_default(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module.config, "ADMIN_API_KEY", None)

    resp = client.get("/admin/flush-cache")

    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
