"""
Ingestion specific pipeline handlers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog
from pydantic import ValidationError

from config import settings
from kafka_producer import KafkaProducerClient
from libs.shared.models import IoTEvent
from libs.shared.pipeline import Handler
from metrics import (
    EVENTS_DROPPED,
    EVENTS_RECEIVED,
    EVENTS_VALIDATED,
    VALIDATION_LATENCY,
)
from rate_limiter import RateLimiter

log = structlog.get_logger(__name__)

@dataclass
class IngestionContext:
    """Context passed through the ingestion pipeline."""
    raw_message: dict[str, Any]
    device_id: str = "unknown"
    event: IoTEvent | None = None
    processed: bool = False
    errors: list[str] = field(default_factory=list)

class LoggingHandler:
    """Initial logging and device_id extraction."""
    async def __call__(self, context: IngestionContext, next_call: Callable[[], Awaitable[None]]) -> None:
        topic = context.raw_message["topic"]
        parts = topic.split("/")
        context.device_id = parts[1] if len(parts) >= 2 else "unknown"
        
        try:
            raw_payload = json.loads(context.raw_message["payload"])
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            EVENTS_DROPPED.labels(reason="invalid_json").inc()
            log.warning("Invalid JSON payload", device_id=context.device_id, error=str(exc))
            return

        EVENTS_RECEIVED.labels(
            device_id=context.device_id, 
            event_type=raw_payload.get("event_type", "unknown")
        ).inc()
        
        context.raw_message["parsed_payload"] = raw_payload
        await next_call()

class ValidationHandler:
    """Pydantic schema validation."""
    async def __call__(self, context: IngestionContext, next_call: Callable[[], Awaitable[None]]) -> None:
        raw_payload = context.raw_message.get("parsed_payload")
        with VALIDATION_LATENCY.time():
            try:
                context.event = IoTEvent(**raw_payload)
                await next_call()
            except ValidationError as exc:
                EVENTS_DROPPED.labels(reason="schema_error").inc()
                log.warning(
                    "Schema validation failed",
                    device_id=context.device_id,
                    errors=exc.errors(),
                )

class RateLimitHandler:
    """Per-device rate limiting via Redis."""
    def __init__(self, rate_limiter: RateLimiter) -> None:
        self._limiter = rate_limiter

    async def __call__(self, context: IngestionContext, next_call: Callable[[], Awaitable[None]]) -> None:
        if not await self._limiter.is_allowed(context.device_id):
            EVENTS_DROPPED.labels(reason="rate_limited").inc()
            log.warning("Rate limit exceeded", device_id=context.device_id)
            return
        
        await next_call()

class KafkaPublishHandler:
    """Produces the final event to Kafka."""
    def __init__(self, kafka_producer: KafkaProducerClient) -> None:
        self._producer = kafka_producer

    async def __call__(self, context: IngestionContext, next_call: Callable[[], Awaitable[None]]) -> None:
        if not context.event:
            return

        try:
            await self._producer.produce(
                topic=settings.KAFKA_TOPIC_RAW,
                value=context.event.to_kafka_dict(),
                key=context.event.device_id,
            )
            EVENTS_VALIDATED.inc()
            log.info(
                "Event ingested and produced to Kafka",
                event_id=context.event.event_id,
                device_id=context.event.device_id,
                event_type=context.event.event_type,
            )
            context.processed = True
            await next_call()
        except Exception as exc:
            EVENTS_DROPPED.labels(reason="kafka_error").inc()
            log.error(
                "Failed to produce event to Kafka",
                event_id=context.event.event_id,
                error=str(exc),
            )
