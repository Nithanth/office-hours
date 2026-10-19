"""Unit tests for services.limits friendly-copy helpers."""

from services.limits import friendly_error_message, limit_message


def test_limit_message_known_events():
    assert "search limit" in limit_message("search")
    assert "Settings" in limit_message("chat_message")
    assert "report limit" in limit_message("report_generated")


def test_limit_message_unknown_event_falls_back():
    assert limit_message("nonsense") == limit_message("unknown")


def test_friendly_error_maps_auth_failures():
    for raw in (
        "401 Unauthorized",
        "insufficient_quota",
        "Invalid API key provided",
        "authentication failed",
    ):
        msg = friendly_error_message(RuntimeError(raw), "fallback")
        assert "AI backend" in msg, raw


def test_friendly_error_maps_rate_limits():
    msg = friendly_error_message(RuntimeError("HTTP 429 too many requests"), "x")
    assert "busy" in msg


def test_friendly_error_maps_network():
    msg = friendly_error_message(RuntimeError("connection timed out"), "x")
    assert "reach our backend" in msg


def test_friendly_error_unknown_returns_fallback():
    assert friendly_error_message(RuntimeError("weird thing"), "fallback") == "fallback"


def test_friendly_error_never_leaks_raw():
    # Even bizarre provider text must not pass through.
    msg = friendly_error_message(
        RuntimeError("sk-or-v1-abc123 quota exceeded insufficient_quota"), "x"
    )
    assert "sk-or" not in msg
