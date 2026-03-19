"""
Worker Service — Main entrypoint.

Initializes all dependencies (DB pool, Redis, consumer) and starts the
Kafka consumer loop. Handles graceful shutdown on SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as aioredis
import structlog

import db
from config import settings
from consumer import run_consumer_loop
from metrics import start_metrics_server

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
from libs.shared.logging import configure_logging
configure_logging(settings.ENV, settings.LOG_LEVEL)
log = structlog.get_logger()


async def main() -> None:
    log.info("IoTFlow Worker starting", worker_id=settings.WORKER_ID, env=settings.ENV)

    # Start Prometheus metrics endpoint
    start_metrics_server()
    log.info("Prometheus metrics server started", port=settings.METRICS_PORT)

    # Initialize PostgreSQL pool
    await db.init_pool()
    log.info("PostgreSQL pool initialized")

    # Initialize Redis
    redis_client = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    try:
        await redis_client.ping()
        log.info("Redis connected", url=settings.REDIS_URL)
    except Exception as exc:
        log.warning("Redis unavailable at startup — idempotency will degrade", error=str(exc))

    # Graceful shutdown flag
    stop_event = asyncio.Event()

    def _handle_signal(sig: signal.Signals) -> None:
        log.info("Shutdown signal received", signal=sig.name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        # Run consumer loop until stop signal
        consumer_task = asyncio.create_task(
            run_consumer_loop(redis_client), name="kafka-consumer"
        )

        # Wait for either consumer to fail or stop signal
        done, pending = await asyncio.wait(
            [consumer_task, asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    finally:
        await db.close_pool()
        await redis_client.aclose()
        log.info("IoTFlow Worker shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
