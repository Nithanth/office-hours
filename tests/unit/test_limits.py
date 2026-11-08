"""Unit tests for services.limits: friendly copy + atomic daily quotas."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import services.limits as limits
from services.limits import friendly_error_message, limit_message
from services.persistence import models  # noqa: F401 - registers metadata
from services.persistence.database import Base
from services.persistence.models import UsageEvent
from utils import rate_limit

aiosqlite = pytest.importorskip("aiosqlite")


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


# ---------------------------------------------------------------------------
# reserve_daily_quota — atomic check-and-record, per-IP anonymous bucket
# ---------------------------------------------------------------------------

_ANON_REPORT_LIMIT = limits._EVENT_LIMITS["report_generated"][1]
_USER_REPORT_LIMIT = limits._EVENT_LIMITS["report_generated"][0]


@pytest.fixture
async def quota_db(tmp_path, monkeypatch):
    """File-backed SQLite so concurrent sessions share one database."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'q.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _scope():
        async with maker() as session:
            yield session

    owner = {"id": "sess:a"}
    ip = {"addr": "203.0.113.7"}
    monkeypatch.setattr(limits, "session_scope", _scope)
    monkeypatch.setattr(limits, "database_available", lambda: True)
    monkeypatch.setattr(limits, "prefs_owner_id", lambda _req: owner["id"])
    monkeypatch.setattr(rate_limit, "client_ip", lambda _req: ip["addr"])

    async def _no_byok(_req):
        return SimpleNamespace(openrouter_api_key=None)

    monkeypatch.setattr(limits, "load_model_context", _no_byok)

    async def _count() -> int:
        async with maker() as session:
            return int(await session.scalar(select(func.count(UsageEvent.id))) or 0)

    yield SimpleNamespace(owner=owner, ip=ip, count=_count)
    await engine.dispose()


async def test_reserve_records_until_cap(quota_db):
    for _ in range(_ANON_REPORT_LIMIT):
        assert await limits.reserve_daily_quota(None, "report_generated") is None
    msg = await limits.reserve_daily_quota(None, "report_generated")
    assert msg == limit_message("report_generated")
    assert await quota_db.count() == _ANON_REPORT_LIMIT


async def test_fresh_cookie_does_not_reset_anon_ip_quota(quota_db, monkeypatch):
    monkeypatch.setattr(limits.config, "BETA_ANON_IP_LIMIT_MULTIPLIER", 2)
    ip_cap = _ANON_REPORT_LIMIT * 2
    allowed = 0
    for i in range(ip_cap + 5):
        quota_db.owner["id"] = f"sess:fresh-{i}"  # cookie dropped every call
        if await limits.reserve_daily_quota(None, "report_generated") is None:
            allowed += 1
    assert allowed == ip_cap

    quota_db.ip["addr"] = "198.51.100.9"
    assert await limits.reserve_daily_quota(None, "report_generated") is None


async def test_signed_in_users_skip_anon_ip_bucket(quota_db, monkeypatch):
    monkeypatch.setattr(limits.config, "BETA_ANON_IP_LIMIT_MULTIPLIER", 1)
    for i in range(_ANON_REPORT_LIMIT):
        quota_db.owner["id"] = f"sess:x{i}"
        assert await limits.reserve_daily_quota(None, "report_generated") is None
    quota_db.owner["id"] = "sess:another"
    assert await limits.reserve_daily_quota(None, "report_generated") is not None
    quota_db.owner["id"] = "user-123"
    assert await limits.reserve_daily_quota(None, "report_generated") is None


async def test_parallel_requests_cannot_race_past_cap(quota_db):
    quota_db.owner["id"] = "user-race"
    n = _USER_REPORT_LIMIT
    results = await asyncio.gather(
        *(limits.reserve_daily_quota(None, "report_generated") for _ in range(n + 5))
    )
    assert sum(r is None for r in results) == n
    assert await quota_db.count() == n


def _request(headers: dict[str, str], peer: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
    )


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    monkeypatch.setattr(rate_limit._cfg.config, "TRUST_PROXY_HEADERS", False)
    req = _request({"X-Forwarded-For": "1.2.3.4"})
    assert rate_limit.client_ip(req) == "10.0.0.1"


def test_client_ip_uses_rightmost_hop_behind_trusted_proxy(monkeypatch):
    monkeypatch.setattr(rate_limit._cfg.config, "TRUST_PROXY_HEADERS", True)
    req = _request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert rate_limit.client_ip(req) == "203.0.113.7"
    assert rate_limit.client_ip(_request({})) == "10.0.0.1"
