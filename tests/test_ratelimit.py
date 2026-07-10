"""Rate limiter: window counting, 429 on breach, fail-open when Redis is down."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.core.ratelimit as rl


class FakeRedis:
    def __init__(self):
        self.counts = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, ttl):
        pass


class BrokenRedis:
    async def incr(self, key):
        raise ConnectionError("redis down")


def _request(ip="1.2.3.4"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


async def test_allows_up_to_limit_then_429(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rl, "_get_redis", lambda: fake)
    check = rl.rate_limit("login", times=3, window=60)

    for _ in range(3):
        await check(_request())          # under the limit: no exception
    with pytest.raises(HTTPException) as exc:
        await check(_request())
    assert exc.value.status_code == 429


async def test_limits_are_per_ip(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rl, "_get_redis", lambda: fake)
    check = rl.rate_limit("login", times=1, window=60)

    await check(_request("1.1.1.1"))
    await check(_request("2.2.2.2"))     # different IP: fresh budget
    with pytest.raises(HTTPException):
        await check(_request("1.1.1.1"))


async def test_fails_open_when_redis_down(monkeypatch):
    monkeypatch.setattr(rl, "_get_redis", lambda: BrokenRedis())
    check = rl.rate_limit("login", times=1, window=60)
    # Redis being down must never block authentication.
    for _ in range(5):
        await check(_request())
