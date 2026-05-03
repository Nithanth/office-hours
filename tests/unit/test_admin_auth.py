from __future__ import annotations

import pytest
from fastapi import HTTPException, status

import config as config_module
from api.routes.admin import _require_admin


def test_admin_requires_key_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.config, "ENV", "prod")
    monkeypatch.setattr(config_module.config, "ADMIN_API_KEY", None)

    with pytest.raises(HTTPException) as exc_info:
        _require_admin(None)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
