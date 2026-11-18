from html import unescape
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.routes import ask_llm, author, pages, search, similar
from pages.main_page import render_error_message
from services.core.exa_client import ExaClient
from services.limits import friendly_error_message


@pytest.mark.parametrize(
    "error",
    ["401 invalid api key", "insufficient_quota", "429 rate limit", "network outage"],
)
def test_provider_failure_propagates_without_caching(monkeypatch, error):
    monkeypatch.setattr(
        "services.core.exa_client.ExaAdapter",
        lambda api_key: SimpleNamespace(
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
        assert friendly_error_message(error, "") in unescape(response.text)
        assert "No papers found" not in response.text
        assert 'class="error-message error-context-general"' in response.text
        assert "oh-toast" not in response.text
        assert response.headers["HX-Retarget"] == "#search-results-container"
    else:
        assert "No papers found" in response.text


@pytest.mark.parametrize("error", [None, "401", "429", "network outage"])
def test_home_initial_search_distinguishes_empty_and_failure(
    client, monkeypatch, error
):
    monkeypatch.setattr("pages.main_page.render_error_message", render_error_message)
    monkeypatch.setattr(
        pages,
        "get_exa_client",
        lambda: SimpleNamespace(
            search_and_cache=Mock(
                side_effect=RuntimeError(error) if error else None, return_value=[]
            ),
            reset_cached_search=lambda: None,
        ),
    )
    response = client.get("/", params={"search_query": "neural fields"})
    assert response.status_code == 200
    if error:
        assert friendly_error_message(error, "") in unescape(response.text)
        assert "No papers found" not in response.text
    else:
        assert "No papers found" in response.text


def test_provider_zero_results_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "services.core.exa_client.ExaAdapter",
        lambda api_key: SimpleNamespace(
            sync_client=None, search=Mock(return_value=SimpleNamespace(results=[]))
        ),
    )
    assert ExaClient(api_key="dummy").search_and_cache("no matching papers") == []


def test_similar_provider_failure_is_friendly(client, monkeypatch):
    monkeypatch.setattr(
        similar,
        "get_exa_client",
        lambda: SimpleNamespace(
            find_similar_papers=Mock(
                side_effect=RuntimeError("429 rate limit private provider body")
            )
        ),
    )
    response = client.post(
        "/api/similar", data={"paper_url": "https://arxiv.org/abs/2003.08934"}
    )
    assert response.status_code == 200
    assert "private provider body" not in response.text
    assert friendly_error_message("429", "") in unescape(response.text)


@pytest.mark.parametrize(
    "case", ["missing_key", "empty", "quota", "date", "direct", "escaped"]
)
def test_search_errors_are_persistent(client, monkeypatch, case):
    import config
    import services.limits

    data = {"search_query": "neural fields", "mode": "topic"}
    status = 200
    if case == "missing_key":
        monkeypatch.setattr(config.config, "EXA_API_KEY", "")
    elif case == "empty":
        data["search_query"] = " "
    elif case == "quota":

        async def quota(*args, **kwargs):
            return "Limit reached <try tomorrow> & retry"

        monkeypatch.setattr(services.limits, "reserve_daily_quota", quota)
        status = 429
    elif case == "date":
        data["start_published_date"] = "invalid-date"
    else:
        monkeypatch.setattr(
            search,
            "get_exa_client",
            lambda: SimpleNamespace(
                search_and_cache=Mock(side_effect=RuntimeError("401"))
            ),
        )
        if case == "direct":
            data["mode"] = "direct"
            monkeypatch.setattr(
                search, "get_llm_service", lambda: SimpleNamespace(_initialized=True)
            )
            monkeypatch.setattr(
                search, "ReportGenerator", Mock(side_effect=RuntimeError("401"))
            )
            status = 500
        else:
            monkeypatch.setattr(
                search, "friendly_error_message", lambda *args: "Use <safe> & retry"
            )
    response = client.post("/search", data=data)
    assert response.status_code == status
    assert 'class="error-message error-context-general"' in response.text
    assert "oh-toast" not in response.text
    if case in ("escaped", "quota"):
        assert "&lt;" in response.text and "&amp;" in response.text
        assert "<safe>" not in response.text and "<try tomorrow>" not in response.text


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "search_failure",
        "profile_failure",
        "missing",
        "llm_unavailable",
        "fetch_failure",
        "research_missing",
        "generation_failure",
    ],
)
def test_author_search_flow_errors_are_persistent(client, monkeypatch, case):
    from unittest.mock import AsyncMock

    path = "/author/search"
    data = {"search_query": "Ada Lovelace"}
    status = 200
    if case == "empty":
        data["search_query"] = ""
    elif case == "search_failure":
        monkeypatch.setattr(
            author, "search_authors", Mock(side_effect=RuntimeError("failure"))
        )
    else:
        path = (
            "/author/profile"
            if case in ("profile_failure", "missing")
            else "/author/research-profile"
        )
        data = {"author_id": "1", "author_name": "<script>alert(1)</script>"}
        monkeypatch.setattr(
            author, "get_llm_service", lambda: SimpleNamespace(_initialized=True)
        )
        monkeypatch.setattr(author, "get_author_profile", Mock(return_value=None))
        if case in ("profile_failure", "fetch_failure"):
            monkeypatch.setattr(
                author, "get_author_profile", Mock(side_effect=RuntimeError("failure"))
            )
        elif case == "llm_unavailable":
            monkeypatch.setattr(author, "get_llm_service", lambda: None)
            status = 503
        elif case == "generation_failure":
            profile = SimpleNamespace(
                author=SimpleNamespace(
                    name="Ada", paper_count=1, citation_count=1, h_index=1
                ),
                papers=[],
            )
            monkeypatch.setattr(
                author, "get_author_profile", Mock(return_value=profile)
            )
            monkeypatch.setattr(
                author,
                "get_llm_service",
                lambda: SimpleNamespace(
                    _initialized=True,
                    generate_completion=AsyncMock(side_effect=RuntimeError("failure")),
                ),
            )
    response = client.post(path, data=data)
    assert response.status_code == status
    assert 'class="error-message error-context-general"' in response.text
    assert "oh-toast" not in response.text
    assert "<script>" not in response.text


def test_legacy_ask_error_is_persistent(client, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        ask_llm, "get_llm_service", lambda: SimpleNamespace(_initialized=True)
    )
    monkeypatch.setattr(ask_llm, "get_exa_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        ask_llm,
        "ReportGenerator",
        lambda **kwargs: SimpleNamespace(
            generate_direct_query_answer=AsyncMock(side_effect=RuntimeError("401"))
        ),
    )
    response = client.post("/ask-llm", data={"question": "neural fields"})
    assert response.status_code == 500
    assert 'class="error-message error-context-general"' in response.text
    assert "oh-toast" not in response.text
    assert friendly_error_message("401", "") in unescape(response.text)
