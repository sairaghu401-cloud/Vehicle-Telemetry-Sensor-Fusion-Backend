"""
Redis-backed "current state" for devices — the latest known reading per
device, kept separately from Postgres's full history.

Backend concept: this is a cache, but not a cache of a slow computation —
it's a cache of "the most recent write," which is a very cheap and common
Redis pattern. Postgres is the source of truth (nothing here is ever the
ONLY copy of the data — every reading is written to Postgres first); Redis
just answers "what's the latest?" without needing an ORDER BY ... LIMIT 1
query per device on every dashboard refresh.
"""
import json
from datetime import datetime

import redis.asyncio as redis

LATEST_READING_KEY = "device:{device_id}:latest"

# How long a "latest reading" is considered valid before Redis expires it
# automatically. If a device goes offline, its last reading shouldn't be
# shown as "current" forever — after 5 minutes of silence, the key
# disappears and a dashboard querying it gets nothing (which it should
# interpret as "no recent data," a meaningfully different state from "the
# device is at rest at this exact spot").
LATEST_READING_TTL_SECONDS = 300


async def set_latest_reading(
    redis_client: redis.Redis,
    device_id: str,
    sensor_type: str,
    recorded_at: datetime,
    distance_m: float | None,
    latitude: float | None,
    longitude: float | None,
) -> None:
    key = LATEST_READING_KEY.format(device_id=device_id)
    payload = {
        "sensor_type": sensor_type,
        "recorded_at": recorded_at.isoformat(),
        "distance_m": distance_m,
        "latitude": latitude,
        "longitude": longitude,
    }
    # SET with ex= is one round trip that writes the value AND sets its
    # expiry atomically — avoids a race where another process could read
    # the key in the gap between a separate SET and EXPIRE call.
    await redis_client.set(key, json.dumps(payload), ex=LATEST_READING_TTL_SECONDS)


async def get_latest_reading(redis_client: redis.Redis, device_id: str) -> dict | None:
    key = LATEST_READING_KEY.format(device_id=device_id)
    raw = await redis_client.get(key)
    if raw is None:
        return None
    return json.loads(raw)
