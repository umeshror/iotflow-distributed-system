"""
Worker specific pipeline handlers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import asyncpg
import structlog

import db
from idempotency import IdempotencyGuard
from libs.shared.models import IoTEvent
from libs.shared.pipeline import Handler
from metrics import (
    ACTIVE_TASKS,
    DB_ERRORS,
    DB_WRITE_LATENCY,
    EVENTS_PROCESSED,
    PROCESSING_LATENCY,
)
from retry import DLQPublisher, with_retry

log = structlog.get_logger(__name__)

@dataclass
class WorkerContext:
    """Context passed through the worker pipeline."""
    raw_event: dict[str, Any]
    kafka_partition: int | None = None
    kafka_offset: int | None = None
    event: IoTEvent | None = None
    start_time: float = field(default_factory=time.monotonic)
    processed: bool = False

class IdempotencyHandler:
    """Checks if the event has already been processed."""
    def __init__(self, guard: IdempotencyGuard) -> None:
        self._guard = guard

    async def __call__(self, context: WorkerContext, next_call: Callable[[], Awaitable[None]]) -> None:
        event_id = context.raw_event.get("event_id")
        if not event_id:
            # If no event_id, we can't check idempotency, but we can't really parse either.
            # We'll let the next handlers (Validation) deal with it.
            await next_call()
            return

        if not await self._guard.is_new(event_id):
            log.debug("Duplicate event skipped", event_id=event_id)
            return
        
        await next_call()

class ValidationHandler:
    """Parses and validates the raw event."""
    def __init__(self, dlq: DLQPublisher) -> None:
        self._dlq = dlq

    async def __call__(self, context: WorkerContext, next_call: Callable[[], Awaitable[None]]) -> None:
        event_id = context.raw_event.get("event_id", "unknown")
        try:
            context.event = IoTEvent(**context.raw_event)
            await next_call()
        except Exception as exc:
            log.warning("Event failed model validation — sending to DLQ", event_id=event_id, error=str(exc))
            await self._dlq.send(
                original_event=context.raw_event,
                error_type="VALIDATION_ERROR",
                error_message=str(exc),
                retry_count=0,
                original_partition=context.kafka_partition,
                original_offset=context.kafka_offset,
            )

class PersistenceHandler:
    """Persists the event to PostgreSQL with retry and DLQ fallback."""
    def __init__(self, dlq: DLQPublisher, idempotency: IdempotencyGuard) -> None:
        self._dlq = dlq
        self._idempotency = idempotency

    async def __call__(self, context: WorkerContext, next_call: Callable[[], Awaitable[None]]) -> None:
        if not context.event:
            return

        event = context.event
        retry_count = 0

        async def _insert() -> None:
            nonlocal retry_count
            retry_count += 1
            with DB_WRITE_LATENCY.time():
                await db.insert_event(
                    event_id=event.event_id,
                    device_id=event.device_id,
                    event_type=event.event_type,
                    schema_version=event.schema_version,
                    payload=event.payload,
                    metadata=event.metadata,
                    raw_timestamp=event.timestamp,
                )

        try:
            await with_retry(_insert)
            await next_call()
        except asyncpg.PostgresError as exc:
            DB_ERRORS.labels(error_type=type(exc).__name__).inc()
            log.error("DB error after retries — sending to DLQ", event_id=event.event_id, error=str(exc))
            await self._idempotency.release(event.event_id)
            await self._dlq.send(
                original_event=context.raw_event,
                error_type="DB_ERROR",
                error_message=str(exc),
                retry_count=retry_count,
                original_partition=context.kafka_partition,
                original_offset=context.kafka_offset,
            )
        except Exception as exc:
            log.error("Unexpected error after retries — sending to DLQ", event_id=event.event_id, error=str(exc))
            await self._idempotency.release(event.event_id)
            await self._dlq.send(
                original_event=context.raw_event,
                error_type=type(exc).__name__,
                error_message=str(exc),
                retry_count=retry_count,
                original_partition=context.kafka_partition,
                original_offset=context.kafka_offset,
            )

class DeviceStateHandler:
    """Best-effort update of the device state."""
    async def __call__(self, context: WorkerContext, next_call: Callable[[], Awaitable[None]]) -> None:
        if not context.event:
            return

        event = context.event
        try:
            await db.upsert_device_state(
                device_id=event.device_id,
                event_id=event.event_id,
                event_type=event.event_type,
                last_seen_at=event.timestamp,
                last_payload=event.payload,
            )
        except Exception as exc:
            log.warning("Failed to upsert device state (non-critical)", device_id=event.device_id, error=str(exc))
        
        # Mark as processed successfully
        EVENTS_PROCESSED.labels(event_type=event.event_type).inc()
        duration = time.monotonic() - context.start_time
        log.info(
            "Event processed successfully",
            event_id=event.event_id,
            device_id=event.device_id,
            duration_ms=round(duration * 1000, 2),
        )
        context.processed = True
        await next_call()
