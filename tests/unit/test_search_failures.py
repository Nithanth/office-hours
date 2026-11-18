from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.routes import pages, search
from services.core.exa_client import ExaClient
from services.limits import friendly_error_message


@pytest.mark.parametrize(
    "error",
    ["401 invalid api key", "insufficient_quota", "429 rate limit", "network outage"],
)
def test_provider_failure_propagates_without_caching(monkeypatch, error):
    monkeypatch.setattr(
        "services.core.exa_client.ExaAdapter",
        lambda _: SimpleNamespace(
            sync_client=None, search=Mock(side_effect=RuntimeError(error))
        ),
    )
    client = ExaClient(api_key="dummy")
    with pytest.raises(RuntimeError, match=error):
        client.search_and_cache("neural fields")
    assert client.get_cached_search() is None


@pytest.mark.parametrize(
    "error",
    [
        None,
        "401 invalid api key",
        "429 rate limit",
        "network outage",
        "insufficient_quota",
    ],
)
def test_search_route_distinguishes_empty_and_failure(client, monkeypatch, error):
    provider = SimpleNamespace(
        search_and_cache=Mock(
            side_effect=RuntimeError(error) if error else None, return_value=[]
        )
    )
    monkeypatch.setattr(search, "get_exa_client", lambda: provider)
    response = client.post(
        "/search", data={"search_query": "neural fields", "mode": "topic"}
    )
    assert response.status_code == 200
    if error:
        assert friendly_error_message(error, "") in response.text
        assert "No papers found" not in response.text
    else:
        assert "No papers found" in response.text


def test_home_initial_search_failure_is_friendly(client, monkeypatch):
    monkeypatch.setattr(
        pages,
        "get_exa_client",
        lambda: SimpleNamespace(
            search_and_cache=Mock(side_effect=RuntimeError("401")),
            reset_cached_search=lambda: None,
        ),
    )
    response = client.get("/", params={"search_query": "neural fields"})
    assert response.status_code == 200
    assert friendly_error_message("401", "") in response.text
    assert "No papers found" not in response.text
