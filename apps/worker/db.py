import json
import logging
from datetime import datetime

import asyncpg

from config import settings

import structlog

logger = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=settings.DB_POOL_MIN_SIZE,
        max_size=settings.DB_POOL_MAX_SIZE,
        command_timeout=settings.DB_COMMAND_TIMEOUT,
        server_settings={"application_name": settings.SERVICE_NAME},
    )
    logger.info("DB connection pool created", pool_size=settings.DB_POOL_MAX_SIZE)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        logger.info("DB connection pool closed")


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool


async def insert_event(
    event_id: str,
    device_id: str,
    event_type: str,
    schema_version: str,
    payload: dict,
    metadata: dict,
    raw_timestamp: datetime,
) -> bool:
    """
    Insert an event into the partitioned events table.

    Returns True if a row was inserted, False if the event was a duplicate
    (ON CONFLICT DO NOTHING — relies on unique (event_id, ingested_at) PK).
    """
    pool = get_pool()
    result = await pool.execute(
        """
        INSERT INTO events (
            event_id, device_id, event_type, schema_version,
            payload, metadata, raw_timestamp, processed_at, status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), 'processed')
        ON CONFLICT (event_id, ingested_at) DO NOTHING
        """,
        event_id,
        device_id,
        event_type,
        schema_version,
        json.dumps(payload),
        json.dumps(metadata),
        raw_timestamp,
    )
    # asyncpg returns "INSERT 0 N" — extract N
    rows_affected = int(result.split()[-1])
    return rows_affected > 0


async def upsert_device_state(
    device_id: str,
    event_id: str,
    event_type: str,
    last_seen_at: datetime,
    last_payload: dict,
) -> None:
    """
    Upsert the materialised device latest-state row.
    Ensures the device exists in the 'devices' table first to satisfy FK.
    """
    pool = get_pool()
    
    # 1. Ensure device exists (blind insert)
    await pool.execute(
        "INSERT INTO devices (device_id, device_type) VALUES ($1, 'sensor') ON CONFLICT DO NOTHING",
        device_id,
    )

    # 2. Upsert state
    await pool.execute(
        """
        INSERT INTO device_state (
            device_id, last_event_id, last_event_type, last_seen_at,
            last_payload, event_count
        ) VALUES ($1, $2, $3, $4, $5, 1)
        ON CONFLICT (device_id) DO UPDATE SET
            last_event_id   = EXCLUDED.last_event_id,
            last_event_type = EXCLUDED.last_event_type,
            last_seen_at    = EXCLUDED.last_seen_at,
            last_payload    = EXCLUDED.last_payload,
            event_count     = device_state.event_count + 1
        """,
        device_id,
        event_id,
        event_type,
        last_seen_at,
        json.dumps(last_payload),
    )


async def record_dlq_event(
    event_id: str,
    device_id: str | None,
    original_topic: str,
    raw_payload: dict,
    error_message: str,
    retry_count: int,
) -> None:
    """Persist a DLQ entry for audit and replay tracking."""
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO dlq_events (
            event_id, device_id, original_topic, raw_payload,
            error_message, retry_count
        ) VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        event_id,
        device_id,
        original_topic,
        json.dumps(raw_payload),
        error_message,
        retry_count,
    )
