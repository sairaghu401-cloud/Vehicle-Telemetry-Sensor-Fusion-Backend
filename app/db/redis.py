"""
Redis client setup.

Backend concept: Redis here is used as a fast in-memory key-value store for
"current state" — e.g. "what was the last known position/speed of device X"
— which dashboards can poll cheaply without hitting Postgres. Postgres stays
the source of truth for historical, durable data; Redis is a cache in front
of / alongside it, optimized for very fast reads of the latest value.
"""
import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

redis_client = redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis():
    """FastAPI dependency that yields the shared Redis client."""
    yield redis_client
