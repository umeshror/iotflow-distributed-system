"""
Redis-based idempotency guard — v2 (50K–100K events/sec).

v1: Single SET NX per event (~0.5–2ms RTT). At 100K/s = 100,000 RTTs/s — impossible.

v2 changes:
- Added are_new_batch(): pipelines N SET NX commands in a single round-trip.
  At batch=200, throughput = 200 checks / 2ms RTT = 100,000 checks/sec per connection.
- Kept is_new() for single-event paths and graceful Redis-down fallback.
- are_new_batch() also uses pipeline — same fail-open semantics on error.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from config import settings
from metrics import EVENTS_DUPLICATE, IDEMPOTENCY_CHECK_LATENCY

logger = logging.getLogger(__name__)


class IdempotencyGuard:
    """
    Redis SET NX idempotency check.

    Returns:
        True  → event has NOT been seen; caller should process it
        False → event IS a duplicate; caller should skip it
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    def _key(self, event_id: str) -> str:
        return f"iotflow:idempotency:{event_id}"

    async def is_new(self, event_id: str) -> bool:
        """
        Attempts to atomically claim the event_id.
        Returns True if successfully claimed (first time seeing this event).
        Returns False if the key already existed (duplicate).
        On Redis failure: returns True (fail-open, DB layer handles dedup).
        """
        with IDEMPOTENCY_CHECK_LATENCY.time():
            try:
                result = await self._redis.set(
                    self._key(event_id),
                    "1",
                    nx=True,
                    ex=settings.IDEMPOTENCY_TTL_SECONDS,
                )
                if result is None:
                    # Key already existed — duplicate
                    EVENTS_DUPLICATE.inc()
                    logger.debug("Duplicate event detected", extra={"event_id": event_id})
                    return False
                return True
            except Exception as exc:
                logger.warning(
                    "Redis idempotency check failed — falling through to DB",
                    extra={"event_id": event_id, "error": str(exc)},
                )
                return True  # fail-open

    async def are_new_batch(self, event_ids: list[str]) -> list[bool]:
        """
        v2 HIGH-THROUGHPUT PATH — Pipeline N idempotency SET NX ops in one RTT.

        Instead of N × 2ms = high latency at scale, this fires all SET NX
        commands in a single pipeline and awaits one response.

        At batch_size=200: 200 checks / 2ms RTT = 100,000 checks/sec/connection.

        Returns:
            list[bool]: True = new event (process it), False = duplicate (skip)

        On Redis failure: returns all-True (fail-open — DB deduplicates).
        """
        if not event_ids:
            return []

        with IDEMPOTENCY_CHECK_LATENCY.time():
            try:
                async with self._redis.pipeline(transaction=False) as pipe:
                    for event_id in event_ids:
                        # Each SET NX is queued but not sent until execute()
                        pipe.set(
                            self._key(event_id),
                            "1",
                            nx=True,
                            ex=settings.IDEMPOTENCY_TTL_SECONDS,
                        )
                    results = await pipe.execute()
                # result is True (key newly set) or None (key already existed)
                outcomes = []
                for event_id, r in zip(event_ids, results):
                    if r is None:
                        EVENTS_DUPLICATE.inc()
                        logger.debug(
                            "Duplicate event detected (batch)",
                            extra={"event_id": event_id},
                        )
                        outcomes.append(False)
                    else:
                        outcomes.append(True)
                return outcomes
            except Exception as exc:
                logger.warning(
                    "Redis batch idempotency check failed — fail-open for entire batch",
                    extra={"batch_size": len(event_ids), "error": str(exc)},
                )
                return [True] * len(event_ids)  # fail-open

    async def release(self, event_id: str) -> None:
        """
        Release an idempotency key (used only on DLQ path to allow replay).
        Normally keys are NOT released; they expire after TTL.
        """
        try:
            await self._redis.delete(self._key(event_id))
        except Exception:
            pass  # best-effort
