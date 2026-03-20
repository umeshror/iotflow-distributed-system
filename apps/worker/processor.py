"""
Event Processor — core business logic for a single IoT event.
Now implemented using a modular Pipeline of handlers.
"""

from __future__ import annotations

import structlog
import time
from typing import Any

from handlers import (
    WorkerContext,
    IdempotencyHandler,
    ValidationHandler,
    PersistenceHandler,
    DeviceStateHandler,
)
from idempotency import IdempotencyGuard
from libs.shared.pipeline import Pipeline
from metrics import ACTIVE_TASKS, PROCESSING_LATENCY
from retry import DLQPublisher

log = structlog.get_logger(__name__)


class EventProcessor:
    def __init__(
        self,
        idempotency_guard: IdempotencyGuard,
        dlq_publisher: DLQPublisher,
    ) -> None:
        self._idempotency = idempotency_guard
        self._dlq = dlq_publisher
        
        # Initialize processing pipeline
        self._pipeline = Pipeline[WorkerContext]()
        self._pipeline.use(IdempotencyHandler(self._idempotency))
        self._pipeline.use(ValidationHandler(self._dlq))
        self._pipeline.use(PersistenceHandler(self._dlq, self._idempotency))
        self._pipeline.use(DeviceStateHandler())

    async def process(
        self,
        raw_event: dict[str, Any],
        kafka_partition: int | None = None,
        kafka_offset: int | None = None,
    ) -> None:
        """
        Process a single event from Kafka using the pipeline.
        All exceptions are caught and routed to DLQ — this method never raises.
        """
        ACTIVE_TASKS.inc()

        try:
            with PROCESSING_LATENCY.time():
                trace_id = raw_event.get("_trace_id", "")
                context = WorkerContext(
                    raw_event=raw_event,
                    trace_id=trace_id,
                    kafka_partition=kafka_partition,
                    kafka_offset=kafka_offset,
                )
                await self._pipeline.execute(context)
        finally:
            ACTIVE_TASKS.dec()
