"""
Exponential backoff retry decorator + Dead Letter Queue (DLQ) publisher.

Design:
  - Retry transient errors (DB, Redis, network) up to RETRY_MAX_ATTEMPTS
  - Apply exponential backoff with jitter to avoid thundering herd
  - After all retries exhausted, publish to the DLQ Kafka topic
  - Record the failure in PostgreSQL dlq_events table for audit/replay
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
import structlog
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from config import settings
from metrics import EVENTS_DLQ, RETRY_ATTEMPTS

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Non-retryable exception types — these go directly to DLQ
# ---------------------------------------------------------------------------
NON_RETRYABLE_ERRORS = (
    ValueError,       # schema validation errors
    json.JSONDecodeError,
)


def _compute_delay(attempt: int) -> float:
    """
    Exponential backoff with ±25% jitter.
    delay = min(base * multiplier^(attempt-1), max) * jitter
    """
    base = settings.RETRY_BASE_DELAY_SECONDS
    multiplier = settings.RETRY_MULTIPLIER
    max_delay = settings.RETRY_MAX_DELAY_SECONDS
    jitter = settings.RETRY_JITTER_FACTOR

    delay = min(base * (multiplier ** (attempt - 1)), max_delay)
    jitter_range = delay * jitter
    return delay + random.uniform(-jitter_range, jitter_range)


async def with_retry(coro_fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """
    Execute an async callable with exponential backoff retry.

    On non-retryable errors: raises immediately (caller sends to DLQ).
    On retryable errors: retries up to RETRY_MAX_ATTEMPTS.
    After all retries: raises the last exception (caller sends to DLQ).
    """
    last_exc: Exception | None = None

    for attempt in range(1, settings.RETRY_MAX_ATTEMPTS + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except NON_RETRYABLE_ERRORS as exc:
            logger.warning(
                "Non-retryable error — sending to DLQ",
                attempt=attempt,
                error=str(exc),
            )
            raise
        except Exception as exc:
            last_exc = exc
            RETRY_ATTEMPTS.labels(attempt_number=str(attempt)).inc()
            if attempt < settings.RETRY_MAX_ATTEMPTS:
                delay = _compute_delay(attempt)
                logger.warning(
                    f"Transient error: {exc} — retrying",
                    attempt=attempt,
                    max_attempts=settings.RETRY_MAX_ATTEMPTS,
                    delay_seconds=round(delay, 2),
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "All retry attempts exhausted",
                    attempts=settings.RETRY_MAX_ATTEMPTS,
                    error=str(exc),
                )

    assert last_exc is not None
    raise last_exc


class DLQPublisher:
    """
    Publishes failed events to the Kafka DLQ topic and records them in PostgreSQL.
    Initialized with a running AIOKafkaProducer instance.
    """

    def __init__(self, producer: AIOKafkaProducer) -> None:
        self._producer = producer

    async def send(
        self,
        original_event: dict[str, Any],
        error_type: str,
        error_message: str,
        retry_count: int,
        original_topic: str = "iot.events.raw",
        original_partition: int | None = None,
        original_offset: int | None = None,
    ) -> None:
        """
        Publish a DLQ envelope to iot.events.dlq.
        Also persists a record to PostgreSQL via db.record_dlq_event.
        Logs error but does NOT raise if DLQ publish itself fails
        (we cannot retry the DLQ publisher — that way lies madness).
        """
        import db  # local import to avoid circular dependency

        event_id = original_event.get("event_id", "unknown")
        device_id = original_event.get("device_id")

        EVENTS_DLQ.labels(error_type=error_type).inc()

        dlq_envelope = {
            "dlq_id": str(uuid.uuid4()),
            "original_event": original_event,
            "original_topic": original_topic,
            "original_partition": original_partition,
            "original_offset": original_offset,
            "error_type": error_type,
            "error_message": error_message,
            "retry_count": retry_count,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "worker_id": settings.WORKER_ID,
        }

        # Publish to Kafka DLQ topic
        try:
            await self._producer.send_and_wait(
                topic=settings.KAFKA_TOPIC_DLQ,
                value=json.dumps(dlq_envelope).encode("utf-8"),
                key=(event_id or "unknown").encode("utf-8"),
            )
            logger.info(
                "Event sent to DLQ",
                event_id=event_id,
                error_type=error_type,
                retry_count=retry_count,
            )
        except KafkaError as exc:
            logger.error(
                "CRITICAL: Failed to publish to DLQ topic",
                event_id=event_id,
                error=str(exc),
            )

        # Persist to PostgreSQL for audit
        try:
            await db.record_dlq_event(
                event_id=event_id,
                device_id=device_id,
                original_topic=original_topic,
                raw_payload=original_event,
                error_message=f"[{error_type}] {error_message}",
                retry_count=retry_count,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist DLQ record to PostgreSQL",
                event_id=event_id,
                error=str(exc),
            )
