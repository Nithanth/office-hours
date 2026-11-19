import pytest


@pytest.mark.parametrize("condition", ["etag", "weak", "wildcard"])
def test_versioned_js_conditional_response_has_no_body_or_length(client, condition):
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    etag = response.headers["etag"]
    value = {"etag": etag, "weak": f"W/{etag}", "wildcard": "*"}[condition]
    cached = client.get("/static/js/main.js", headers={"If-None-Match": value})
    assert cached.status_code == 304
    assert cached.content == b""
    assert "content-length" not in cached.headers
    assert cached.headers["etag"] == etag


def test_versioned_js_head_matches_get_headers_without_body(client):
    response = client.get("/static/js/main.js")
    head = client.head("/static/js/main.js")
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == response.headers["content-length"]
    assert head.headers["etag"] == response.headers["etag"]
    conditional = client.head(
        "/static/js/main.js", headers={"If-None-Match": head.headers["etag"]}
    )
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert "content-length" not in conditional.headers
