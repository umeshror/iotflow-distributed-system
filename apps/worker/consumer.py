"""
Kafka consumer loop for the Worker Service — v2 (50K–100K events/sec).

Key design decisions (preserved from v1):
- Manual offset commit: offset only committed AFTER successful processing
  (or DLQ publish). This guarantees at-least-once delivery.
- asyncio.Semaphore for bounded concurrency — prevents DB connection pool exhaustion.
- Partition assignment callback logs consumer group rebalance events for debugging.

v2 changes:
- REMOVED per-message consumer.end_offsets() RPC call (BN-4 bottleneck).
  At 50K events/s that was 50,000 ListOffsets RPCs/s — impossible.
  Replaced with _maybe_sample_lag() that fires at most once every 5 seconds.
- MAX_CONCURRENT_TASKS increased from 50 → 100 per pod for higher throughput.
"""

from __future__ import annotations

import asyncio
import json
import structlog
import time
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition, ConsumerRebalanceListener
from aiokafka.errors import KafkaError

from config import settings
from idempotency import IdempotencyGuard
from metrics import CONSUMER_LAG, EVENTS_CONSUMED
from processor import EventProcessor
from retry import DLQPublisher

logger = structlog.get_logger(__name__)

# Semaphore limits in-flight DB writes (backpressure)
_semaphore: asyncio.Semaphore | None = None

# v2: throttle lag metric sampling to avoid per-message broker RPCs
_last_lag_sample: float = 0.0
_LAG_SAMPLE_INTERVAL_SECONDS: float = 5.0


async def run_consumer_loop(
    redis_client: Any,
) -> None:
    """
    Main entry point for the Kafka consumer loop.
    Creates consumer, producer (for DLQ), and runs until cancelled.
    """
    global _semaphore
    _semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)

    # DLQ producer — separate from main consumer, used only for DLQ publishes
    dlq_producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        enable_idempotence=True,
        acks="all",
    )
    await dlq_producer.start()
    logger.info("DLQ producer started")

    idempotency_guard = IdempotencyGuard(redis_client)
    dlq_publisher = DLQPublisher(dlq_producer)
    processor = EventProcessor(idempotency_guard, dlq_publisher)

    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_RAW,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.KAFKA_CONSUMER_GROUP,
        auto_offset_reset=settings.KAFKA_AUTO_OFFSET_RESET,
        enable_auto_commit=False,  # manual commit
        max_poll_records=settings.KAFKA_MAX_POLL_RECORDS,
        session_timeout_ms=settings.KAFKA_SESSION_TIMEOUT_MS,
        heartbeat_interval_ms=settings.KAFKA_HEARTBEAT_INTERVAL_MS,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
    )
    consumer.subscribe(
        [settings.KAFKA_TOPIC_RAW],
        listener=_RebalanceListener(),
    )

    try:
        await consumer.start()
        logger.info(
            "Kafka consumer started",
            topic=settings.KAFKA_TOPIC_RAW,
            group=settings.KAFKA_CONSUMER_GROUP,
        )
        await _consume(consumer, processor)
    except asyncio.CancelledError:
        logger.info("Consumer loop cancelled")
    except KafkaError as exc:
        logger.error("Fatal Kafka error", error=str(exc))
        raise
    finally:
        await consumer.stop()
        await dlq_producer.stop()
        logger.info("Consumer and DLQ producer stopped")


async def _consume(
    consumer: AIOKafkaConsumer,
    processor: EventProcessor,
) -> None:
    """Inner consume loop — processes each message, then manually commits."""
    tasks: set[asyncio.Task] = set()

    async for msg in consumer:
        EVENTS_CONSUMED.inc()

        raw_event: dict = msg.value

        # v2: Throttled lag sampling — at most once per 5s, not per-message.
        # At 50K events/s, per-message end_offsets() = 50,000 broker RPCs/s.
        await _maybe_sample_lag(consumer)

        # Bounded concurrency: acquire semaphore before spawning task
        assert _semaphore is not None
        await _semaphore.acquire()

        async def _task_wrapper(
            event: dict,
            partition: int,
            offset: int,
            tp: TopicPartition,
        ) -> None:
            try:
                await processor.process(
                    raw_event=event,
                    kafka_partition=partition,
                    kafka_offset=offset,
                )
                # Manual commit AFTER successful processing or DLQ publish
                await consumer.commit({tp: offset + 1})
            finally:
                _semaphore.release()

        task = asyncio.create_task(
            _task_wrapper(
                raw_event,
                msg.partition,
                msg.offset,
                tp=TopicPartition(msg.topic, msg.partition),
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)


class _RebalanceListener(ConsumerRebalanceListener):
    """Logs partition assignment changes during consumer group rebalances."""

    async def on_partitions_revoked(self, revoked: set[TopicPartition]) -> None:
        logger.info("Partitions revoked", partitions=[str(p) for p in revoked])

    async def on_partitions_assigned(self, assigned: set[TopicPartition]) -> None:
        logger.info("Partitions assigned", partitions=[str(p) for p in assigned])


async def _maybe_sample_lag(consumer: AIOKafkaConsumer) -> None:
    """
    v2: Sample Kafka consumer lag at most once every LAG_SAMPLE_INTERVAL_SECONDS.

    Replaces the per-message end_offsets() RPC from v1 which caused O(events/s)
    broker requests — a critical bottleneck above ~5K events/sec.
    """
    global _last_lag_sample
    now = time.monotonic()
    if now - _last_lag_sample < _LAG_SAMPLE_INTERVAL_SECONDS:
        return
    _last_lag_sample = now

    try:
        assigned = consumer.assignment()
        if not assigned:
            return
        end_offsets = await consumer.end_offsets(list(assigned))
        for tp, end_offset in end_offsets.items():
            committed = await consumer.committed(tp)
            committed_offset = committed if committed is not None else 0
            lag = max(0, end_offset - committed_offset - 1)
            CONSUMER_LAG.labels(partition=str(tp.partition)).set(lag)
    except Exception as exc:
        logger.debug("Lag sampling error (non-critical)", error=str(exc))
