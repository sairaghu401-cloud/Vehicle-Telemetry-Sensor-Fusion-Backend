"""
Fixed-window rate limiting backed by Redis.

Backend concept: a rate limiter answers "has this caller done this action
too many times recently?" It is what stops someone from trying 100,000
passwords against /auth/login. Each (scope, caller) pair gets a counter in
Redis that expires on its own after the window, so there is no cleanup job
and the state is shared by every API process (an in-memory counter would
reset on restart and differ per container).

Tradeoffs worth knowing about:
- Fixed window, not sliding: a caller can burst up to 2x the limit across a
  window boundary (10 at 0:59, 10 more at 1:00). Fine for brute-force
  protection; a sliding window or token bucket is the upgrade if precise
  limits ever matter.
- Keyed by client IP. Behind a reverse proxy every request can look like it
  comes from the proxy's IP, so run uvicorn with --proxy-headers (and a
  trusted forwarded-allow-ips) once deployed behind one.
- Fails OPEN if Redis is unreachable: login keeps working without rate
  limiting rather than the whole API going down with Redis. For a
  higher-security system you would choose to fail closed instead.
"""
import logging
from dataclasses import dataclass

import redis.asyncio as redis

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


async def check_rate_limit(
    redis_client: redis.Redis, key: str, limit: int, window_seconds: int
) -> RateLimitResult:
    try:
        # SET ... NX EX creates the counter with its expiry in ONE atomic
        # command (a separate SET then EXPIRE could crash in between and
        # leave a counter that never expires). INCR then keeps the TTL.
        await redis_client.set(key, 0, ex=window_seconds, nx=True)
        count = await redis_client.incr(key)
        if count <= limit:
            return RateLimitResult(allowed=True, retry_after_seconds=0)
        ttl = await redis_client.ttl(key)
        return RateLimitResult(allowed=False, retry_after_seconds=max(ttl, 1))
    except redis.RedisError:
        logger.warning("Rate limiter could not reach Redis; allowing request (fail open)")
        return RateLimitResult(allowed=True, retry_after_seconds=0)
