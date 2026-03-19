"""
Event Processor — core business logic for a single IoT event.

Responsibilities:
1. Idempotency check (Redis)
2. Parse and validate the event model
3. Persist to PostgreSQL (with retry via retry.with_retry)
4. Upsert device latest state
5. On failure: publish to DLQ
"""

from __future__ import annotations

import logging
import time
from typing import Any

import asyncpg

import db
from idempotency import IdempotencyGuard
from metrics import (
    ACTIVE_TASKS,
    DB_ERRORS,
    DB_WRITE_LATENCY,
    EVENTS_PROCESSED,
    PROCESSING_LATENCY,
)
from retry import DLQPublisher, with_retry

logger = logging.getLogger(__name__)


class EventProcessor:
    def __init__(
        self,
        idempotency_guard: IdempotencyGuard,
        dlq_publisher: DLQPublisher,
    ) -> None:
        self._idempotency = idempotency_guard
        self._dlq = dlq_publisher

    async def process(
        self,
        raw_event: dict[str, Any],
        kafka_partition: int | None = None,
        kafka_offset: int | None = None,
    ) -> None:
        """
        Process a single event from Kafka.
        All exceptions are caught and routed to DLQ — this method never raises.
        """
        # Lazy import to avoid circular dependency with shared models
        from services.shared.models import IoTEvent

        start = time.monotonic()
        event_id = raw_event.get("event_id", "unknown")
        ACTIVE_TASKS.inc()

        try:
            with PROCESSING_LATENCY.time():
                # ------------------------------------------------------------------
                # 1. Parse & validate
                # ------------------------------------------------------------------
                try:
                    event = IoTEvent(**raw_event)
                except Exception as exc:
                    logger.warning(
                        "Event failed model validation — sending to DLQ",
                        extra={"event_id": event_id, "error": str(exc)},
                    )
                    await self._dlq.send(
                        original_event=raw_event,
                        error_type="VALIDATION_ERROR",
                        error_message=str(exc),
                        retry_count=0,
                        kafka_partition=kafka_partition,
                        kafka_offset=kafka_offset,
                    )
                    return

                # ------------------------------------------------------------------
                # 2. Idempotency check
                # ------------------------------------------------------------------
                if not await self._idempotency.is_new(event.event_id):
                    logger.debug(
                        "Duplicate event skipped",
                        extra={"event_id": event.event_id},
                    )
                    return

                # ------------------------------------------------------------------
                # 3. Persist event with retry + DLQ fallback
                # ------------------------------------------------------------------
                retry_count = 0

                async def _insert() -> None:
                    nonlocal retry_count
                    retry_count += 1
                    with DB_WRITE_LATENCY.time():
                        inserted = await db.insert_event(
                            event_id=event.event_id,
                            device_id=event.device_id,
                            event_type=event.event_type,
                            schema_version=event.schema_version,
                            payload=event.payload,
                            metadata=event.metadata,
                            raw_timestamp=event.timestamp,
                        )
                    return inserted

                try:
                    inserted = await with_retry(_insert)
                except asyncpg.PostgresError as exc:
                    DB_ERRORS.labels(error_type=type(exc).__name__).inc()
                    logger.error(
                        "DB error after retries — sending to DLQ",
                        extra={"event_id": event.event_id, "error": str(exc)},
                    )
                    # Release idempotency key so replay can re-claim it
                    await self._idempotency.release(event.event_id)
                    await self._dlq.send(
                        original_event=raw_event,
                        error_type="DB_ERROR",
                        error_message=str(exc),
                        retry_count=retry_count,
                        kafka_partition=kafka_partition,
                        kafka_offset=kafka_offset,
                    )
                    return
                except Exception as exc:
                    logger.error(
                        "Unexpected error after retries — sending to DLQ",
                        extra={"event_id": event.event_id, "error": str(exc)},
                    )
                    await self._idempotency.release(event.event_id)
                    await self._dlq.send(
                        original_event=raw_event,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        retry_count=retry_count,
                        kafka_partition=kafka_partition,
                        kafka_offset=kafka_offset,
                    )
                    return

                # ------------------------------------------------------------------
                # 4. Upsert device state (best-effort — not critical path)
                # ------------------------------------------------------------------
                try:
                    await db.upsert_device_state(
                        device_id=event.device_id,
                        event_id=event.event_id,
                        event_type=event.event_type,
                        last_seen_at=event.timestamp,
                        last_payload=event.payload,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to upsert device state (non-critical)",
                        extra={"device_id": event.device_id, "error": str(exc)},
                    )

                EVENTS_PROCESSED.labels(event_type=event.event_type).inc()
                elapsed = time.monotonic() - start
                logger.info(
                    "Event processed successfully",
                    extra={
                        "event_id": event.event_id,
                        "device_id": event.device_id,
                        "event_type": event.event_type,
                        "duration_ms": round(elapsed * 1000, 2),
                        "inserted": inserted,
                    },
                )

        finally:
            ACTIVE_TASKS.dec()
