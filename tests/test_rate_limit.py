"""
Tests for the Redis rate limiter (app/services/rate_limit.py) and its
wiring onto /auth/login and /auth/register.

conftest.py flushes the test Redis index after every test, so counters from
one test never leak into the next.
"""
from app.core.config import get_settings
from app.db.redis import redis_client
from app.services.rate_limit import check_rate_limit


async def test_allows_up_to_the_limit_then_blocks():
    results = [await check_rate_limit(redis_client, "ratelimit:t:a", limit=3, window_seconds=60) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert 0 < results[-1].retry_after_seconds <= 60


async def test_different_keys_are_counted_separately():
    for _ in range(3):
        await check_rate_limit(redis_client, "ratelimit:t:one", limit=3, window_seconds=60)
    other = await check_rate_limit(redis_client, "ratelimit:t:two", limit=3, window_seconds=60)
    assert other.allowed


async def test_counter_always_has_an_expiry():
    await check_rate_limit(redis_client, "ratelimit:t:ttl", limit=3, window_seconds=60)
    assert 0 < await redis_client.ttl("ratelimit:t:ttl") <= 60


async def test_login_is_rate_limited_after_too_many_attempts(client):
    limit = get_settings().login_rate_limit_attempts
    creds = {"username": "nobody@example.com", "password": "wrong-password"}

    for _ in range(limit):
        assert (await client.post("/auth/login", data=creds)).status_code == 401

    blocked = await client.post("/auth/login", data=creds)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_rate_limit_applies_before_credentials_are_checked(client, registered_user):
    """Once blocked, even the CORRECT password is refused: the limiter runs
    before the bcrypt check, so a guesser can't keep testing passwords."""
    email, password = registered_user
    limit = get_settings().login_rate_limit_attempts
    for _ in range(limit):
        await client.post("/auth/login", data={"username": email, "password": "wrong"})

    resp = await client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 429


async def test_register_is_rate_limited(client):
    limit = get_settings().register_rate_limit_attempts
    for i in range(limit):
        resp = await client.post(
            "/auth/register", json={"email": f"spam{i}@example.com", "password": "a-strong-password"}
        )
        assert resp.status_code == 201

    blocked = await client.post(
        "/auth/register", json={"email": "one-too-many@example.com", "password": "a-strong-password"}
    )
    assert blocked.status_code == 429


async def test_limiter_fails_open_when_redis_is_unavailable(monkeypatch):
    import redis.asyncio as redis

    async def boom(*args, **kwargs):
        raise redis.ConnectionError("redis down")

    monkeypatch.setattr(redis_client, "set", boom)
    result = await check_rate_limit(redis_client, "ratelimit:t:down", limit=1, window_seconds=60)
    assert result.allowed
