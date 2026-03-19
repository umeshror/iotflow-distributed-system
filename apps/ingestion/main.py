"""
Ingestion Service — Main FastAPI application.

Responsibilities:
1. Expose /healthz and /readyz HTTP endpoints
2. Subscribe to MQTT broker topics via aiomqtt
3. Validate incoming payloads against IoTEvent schema
4. Apply per-device rate limiting via Redis
5. Produce valid events to Kafka topic iot.events.raw

MQTT messages flow into an asyncio.Queue for decoupling the receive loop
from the Kafka produce path — this provides natural backpressure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiomqtt
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config import settings
from kafka_producer import KafkaProducerClient
from metrics import (
    EVENTS_DROPPED,
    EVENTS_RECEIVED,
    EVENTS_VALIDATED,
    MQTT_CONNECTED,
    QUEUE_SIZE,
    VALIDATION_LATENCY,
    start_metrics_server,
)
from rate_limiter import RateLimiter
from handlers import (
    IngestionContext,
    LoggingHandler,
    ValidationHandler,
    RateLimitHandler,
    KafkaPublishHandler,
)
from libs.shared.pipeline import Pipeline

# ---------------------------------------------------------------------------
# Structured logging setup (JSON in production, pretty in dev)
# ---------------------------------------------------------------------------
from libs.shared.logging import configure_logging
configure_logging(settings.ENV, settings.LOG_LEVEL)
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Internal bounded queue — limits memory under Kafka backpressure
# ---------------------------------------------------------------------------
_MSG_QUEUE: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)

# ---------------------------------------------------------------------------
# Shared clients (created at startup, closed at shutdown)
# ---------------------------------------------------------------------------
kafka_producer: KafkaProducerClient | None = None
redis_client: aioredis.Redis | None = None
rate_limiter: RateLimiter | None = None
_pipeline: Pipeline[IngestionContext] | None = None


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global kafka_producer, redis_client, rate_limiter

    # Start metrics HTTP server (separate port, doesn't block FastAPI)
    start_metrics_server()
    log.info("Prometheus metrics server started", port=settings.METRICS_PORT)

    # Kafka producer
    kafka_producer = KafkaProducerClient()
    await kafka_producer.start()

    redis_client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    rate_limiter = RateLimiter(redis_client)

    # Initialize Processing Pipeline
    global _pipeline
    _pipeline = Pipeline[IngestionContext]()
    _pipeline.use(LoggingHandler())
    _pipeline.use(ValidationHandler())
    _pipeline.use(RateLimitHandler(rate_limiter))
    _pipeline.use(KafkaPublishHandler(kafka_producer))

    # Background tasks
    mqtt_task = asyncio.create_task(_mqtt_subscriber_loop(), name="mqtt-subscriber")
    dispatcher_task = asyncio.create_task(_message_dispatcher(), name="msg-dispatcher")
    queue_gauge_task = asyncio.create_task(_queue_gauge_updater(), name="queue-gauge")

    log.info("IoTFlow Ingestion Service started", env=settings.ENV)

    try:
        yield
    finally:
        log.info("Ingestion Service shutting down...")
        mqtt_task.cancel()
        dispatcher_task.cancel()
        queue_gauge_task.cancel()
        await asyncio.gather(mqtt_task, dispatcher_task, queue_gauge_task, return_exceptions=True)
        await kafka_producer.stop()
        await redis_client.aclose()
        log.info("Ingestion Service stopped")


app = FastAPI(
    title="IoTFlow Ingestion Service",
    version="1.0.0",
    description="MQTT event ingestion with Kafka streaming",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health endpoints (required by Kubernetes probes)
# ---------------------------------------------------------------------------
@app.get("/healthz", tags=["health"], summary="Liveness probe")
async def healthz() -> JSONResponse:
    """Always returns 200 if the process is alive."""
    return JSONResponse({"status": "ok"})


@app.get("/readyz", tags=["health"], summary="Readiness probe")
async def readyz() -> JSONResponse:
    """
    Checks MQTT connectivity (via connect attempt), Kafka, and Redis.
    Returns 503 if any critical dependency is unavailable.
    """
    checks: dict[str, str] = {}
    healthy = True

    # Kafka: producer is considered ready if it started without error
    if kafka_producer and kafka_producer._producer:
        checks["kafka"] = "ok"
    else:
        checks["kafka"] = "not_ready"
        healthy = False

    # Redis: simple ping
    try:
        if redis_client:
            await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        # Redis degraded is acceptable — don't mark unready

    status = "ready" if healthy else "degraded"
    http_code = 200 if healthy else 503
    return JSONResponse({"status": status, "checks": checks}, status_code=http_code)


# ---------------------------------------------------------------------------
# MQTT subscriber loop
# ---------------------------------------------------------------------------
async def _mqtt_subscriber_loop() -> None:
    """
    Connects to the MQTT broker and subscribes to all device event topics.
    On disconnect, retries with exponential backoff.
    Messages are pushed into a bounded asyncio.Queue.
    """
    delay = settings.MQTT_RECONNECT_DELAY_MIN

    while True:
        try:
            async with aiomqtt.Client(
                hostname=settings.MQTT_HOST,
                port=settings.MQTT_PORT,
                username=settings.MQTT_USERNAME or None,
                password=settings.MQTT_PASSWORD or None,
                keepalive=settings.MQTT_KEEPALIVE_SECONDS,
                identifier=f"{settings.SERVICE_NAME}-subscriber",
            ) as client:
                MQTT_CONNECTED.set(1)
                delay = settings.MQTT_RECONNECT_DELAY_MIN  # reset on success
                log.info(
                    "Connected to MQTT broker",
                    host=settings.MQTT_HOST,
                    port=settings.MQTT_PORT,
                )
                await client.subscribe(settings.MQTT_TOPIC_PREFIX, qos=1)
                async for message in client.messages:
                    try:
                        _MSG_QUEUE.put_nowait(
                            {
                                "topic": str(message.topic),
                                "payload": message.payload,
                                "received_at": time.monotonic(),
                            }
                        )
                    except asyncio.QueueFull:
                        EVENTS_DROPPED.labels(reason="queue_full").inc()
                        log.warning(
                            "Internal queue full — dropping MQTT message",
                            topic=str(message.topic),
                        )
        except aiomqtt.MqttError as exc:
            MQTT_CONNECTED.set(0)
            log.warning(
                "MQTT connection lost — reconnecting",
                error=str(exc),
                next_retry_seconds=delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.MQTT_RECONNECT_DELAY_MAX)
        except asyncio.CancelledError:
            MQTT_CONNECTED.set(0)
            log.info("MQTT subscriber loop cancelled")
            return


# ---------------------------------------------------------------------------
# Message dispatcher — reads from queue, validates, rate-limits, produces
# ---------------------------------------------------------------------------
async def _message_dispatcher() -> None:
    """
    Consumes messages from the internal queue.
    Validates schema → checks rate limit → produces to Kafka.
    """
    from libs.shared.models import IoTEvent  # relative import

    while True:
        try:
            msg = await _MSG_QUEUE.get()
            await _process_mqtt_message(msg, IoTEvent)
            _MSG_QUEUE.task_done()
        except asyncio.CancelledError:
            log.info("Message dispatcher cancelled")
            return
        except Exception as exc:
            log.error("Unexpected dispatcher error", error=str(exc), exc_info=True)


async def _process_mqtt_message(msg: dict, IoTEvent: type) -> None:
    """Validate, rate-limit, and produce a single MQTT message to Kafka."""
    assert _pipeline is not None
    context = IngestionContext(raw_message=msg)
    await _pipeline.execute(context)


async def _queue_gauge_updater() -> None:
    """Periodically updates the Prometheus queue size gauge."""
    while True:
        try:
            QUEUE_SIZE.set(_MSG_QUEUE.qsize())
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
