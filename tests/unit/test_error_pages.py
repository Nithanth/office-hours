import pytest


@pytest.mark.parametrize(
    "path,status,message",
    [
        ("/not-a-route", 404, "Not Found"),
        ("/auth/google/login", 503, "Google login is not configured."),
        ("/blog-view?token=expired", 404, "Blog not found or expired."),
    ],
)
def test_browser_errors_have_brand_and_recovery(
    client, monkeypatch, path, status, message
):
    import config

    monkeypatch.setattr(config.config, "GOOGLE_CLIENT_ID", None)
    response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert "Office Hours" in response.text
    assert "Back to home" in response.text
    assert message in response.text


@pytest.mark.parametrize(
    "path,accept",
    [
        ("/api/not-a-route", "text/html"),
        ("/not-a-route", "application/json"),
        ("/not-a-route", "*/*"),
    ],
)
def test_non_browser_errors_stay_json(client, path, accept):
    response = client.get(path, headers={"Accept": accept})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_htmx_error_remains_fragment(client):
    response = client.get(
        "/not-a-route", headers={"Accept": "text/html", "HX-Request": "true"}
    )
    assert response.status_code == 404
    assert "error-banner" in response.text
    assert "<html" not in response.text
