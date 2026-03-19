-- =============================================================================
-- IoTFlow — Initial Schema Migration
-- Run once against a fresh PostgreSQL database.
-- Compatible with PostgreSQL 14+
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm"; -- LIKE/iLIKE index support

-- ---------------------------------------------------------------------------
-- ENUM types
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE device_status AS ENUM ('active', 'inactive', 'blocked');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE event_status AS ENUM ('pending', 'processed', 'dlq');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE dlq_status AS ENUM ('pending', 'replaying', 'resolved', 'discarded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- Table: devices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    device_id           TEXT            PRIMARY KEY,
    device_type         TEXT            NOT NULL,
    location            TEXT,
    firmware_version    TEXT,
    tags                TEXT[]          NOT NULL DEFAULT '{}',
    status              device_status   NOT NULL DEFAULT 'active',
    registered_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);
CREATE INDEX IF NOT EXISTS idx_devices_tags   ON devices USING GIN(tags);

-- Auto-update updated_at on every UPDATE
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_devices_updated_at ON devices;
CREATE TRIGGER trg_devices_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Table: events (range-partitioned by ingested_at)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT            NOT NULL,
    device_id           TEXT            NOT NULL,
    event_type          TEXT            NOT NULL,
    schema_version      TEXT            NOT NULL DEFAULT '1.0',
    payload             JSONB           NOT NULL,
    metadata            JSONB           NOT NULL DEFAULT '{}',
    raw_timestamp       TIMESTAMPTZ     NOT NULL,   -- device-reported time
    ingested_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    status              event_status    NOT NULL DEFAULT 'processed',
    processing_error    TEXT,
    PRIMARY KEY (event_id, ingested_at)             -- partition key in PK
) PARTITION BY RANGE (ingested_at);

-- Idempotency: unique event_id within each partition (duplicates silently ignored)
-- ON CONFLICT DO NOTHING works against the partition table

-- Indexes (apply to all partitions via declarative partitioning)
CREATE INDEX IF NOT EXISTS idx_events_device_id  ON events(device_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_status     ON events(status) WHERE status != 'processed';
CREATE INDEX IF NOT EXISTS idx_events_payload    ON events USING GIN(payload);

-- ---------------------------------------------------------------------------
-- Partitions — create 6 months of partitions at migration time.
-- Use pg_partman in production for automatic future partition creation.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events_2026_01 PARTITION OF events
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE IF NOT EXISTS events_2026_02 PARTITION OF events
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE IF NOT EXISTS events_2026_03 PARTITION OF events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE IF NOT EXISTS events_2026_04 PARTITION OF events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS events_2026_05 PARTITION OF events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS events_2026_06 PARTITION OF events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS events_2026_07 PARTITION OF events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS events_2026_08 PARTITION OF events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE IF NOT EXISTS events_2026_09 PARTITION OF events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE IF NOT EXISTS events_2026_10 PARTITION OF events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS events_2026_11 PARTITION OF events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE IF NOT EXISTS events_2026_12 PARTITION OF events
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- ---------------------------------------------------------------------------
-- Table: dlq_events — persistent record of every DLQ entry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dlq_events (
    id              BIGSERIAL       PRIMARY KEY,
    event_id        TEXT            NOT NULL,
    device_id       TEXT,
    original_topic  TEXT            NOT NULL,
    raw_payload     JSONB           NOT NULL,
    error_message   TEXT            NOT NULL,
    retry_count     INT             NOT NULL DEFAULT 0,
    first_seen      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    last_attempted  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    status          dlq_status      NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_dlq_event_id  ON dlq_events(event_id);
CREATE INDEX IF NOT EXISTS idx_dlq_device_id ON dlq_events(device_id);
CREATE INDEX IF NOT EXISTS idx_dlq_status    ON dlq_events(status) WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- Table: device_state — materialised latest state per device
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_state (
    device_id       TEXT        PRIMARY KEY REFERENCES devices(device_id) ON DELETE CASCADE,
    last_event_id   TEXT        NOT NULL,
    last_event_type TEXT        NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    last_payload    JSONB       NOT NULL,
    event_count     BIGINT      NOT NULL DEFAULT 1
);

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
