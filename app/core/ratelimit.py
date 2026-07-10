"""Per-IP rate limiting for sensitive endpoints (login, register, connect_email).

Fixed-window counter in Redis (INCR + EXPIRE): simple, atomic enough for
brute-force protection, and shared across API workers — an in-process counter
would reset per worker and per deploy. Fails OPEN if Redis is unreachable:
losing rate limiting briefly is better than taking authentication down with it.
"""
import logging
import time

from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def rate_limit(scope: str, times: int | None = None, window: int | None = None):
    """FastAPI dependency: allow `times` requests per `window` seconds per client IP."""
    limit = times or settings.auth_rate_limit
    win = window or settings.auth_rate_window_seconds

    async def _check(request: Request) -> None:
        ip = request.client.host if request.client else "unknown"
        key = f"rl:{scope}:{ip}:{int(time.time() // win)}"
        try:
            r = _get_redis()
            count = await r.incr(key)
            if count == 1:
                await r.expire(key, win)
        except Exception as exc:  # noqa: BLE001 — fail open, never block auth on Redis
            logger.warning("rate limiter unavailable (%s) — allowing request", exc)
            return
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait a few minutes and try again.",
            )

    return _check
