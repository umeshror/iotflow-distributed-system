"""
Redis-based sliding-window rate limiter.

Each device gets a per-minute counter stored in Redis.
The Lua script ensures atomicity — no race conditions between INCR and EXPIRE.
Degrades gracefully on Redis failure (logs warning, allows the event through).
"""

from __future__ import annotations

import structlog

import redis.asyncio as aioredis

from config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lua script: atomic INCR + EXPIRE in a single round-trip.
# Returns current count after increment.
# ---------------------------------------------------------------------------
_RATE_LIMIT_LUA = """
local key     = KEYS[1]
local limit   = tonumber(ARGV[1])
local window  = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
if current > limit then
    return 0
end
return 1
"""


class RateLimiter:
    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client
        self._script = redis_client.register_script(_RATE_LIMIT_LUA)

    async def is_allowed(self, device_id: str) -> bool:
        """
        Returns True if the device is within its rate limit, False if exceeded.
        On Redis failure, returns True (fail-open) and logs a warning.
        """
        key = f"iotflow:ratelimit:{device_id}:{settings.RATE_LIMIT_WINDOW_SECONDS}s"
        try:
            result = await self._script(
                keys=[key],
                args=[settings.RATE_LIMIT_MAX_EVENTS, settings.RATE_LIMIT_WINDOW_SECONDS],
            )
            return bool(result)
        except Exception as exc:  # Redis unavailable
            logger.warning(
                "Redis rate limiter unavailable — allowing event through",
                device_id=device_id,
                error=str(exc),
            )
            return True  # fail-open: prefer availability over strict rate limiting
