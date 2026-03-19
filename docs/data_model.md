# IoTFlow — Data Model

## 1. PostgreSQL Schema

### Table: `devices`
```sql
CREATE TABLE devices (
    device_id       TEXT        PRIMARY KEY,            -- human-readable ID from provisioning
    device_type     TEXT        NOT NULL,               -- e.g. "temperature_sensor"
    location        TEXT,
    firmware_version TEXT,
    tags            TEXT[]      DEFAULT '{}',
    status          TEXT        NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','blocked')),
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_tags   ON devices USING GIN(tags);
```

---

### Table: `events` (Partitioned)
```sql
-- Parent table — partitioned by ingestion month
CREATE TABLE events (
    event_id        TEXT        NOT NULL,               -- ULID from device
    device_id       TEXT        NOT NULL REFERENCES devices(device_id),
    event_type      TEXT        NOT NULL,
    schema_version  TEXT        NOT NULL DEFAULT '1.0',
    payload         JSONB       NOT NULL,
    metadata        JSONB       DEFAULT '{}',
    raw_timestamp   TIMESTAMPTZ NOT NULL,               -- as reported by device
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- server-side
    processed_at    TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','processed','dlq')),
    processing_error TEXT,
    PRIMARY KEY (event_id, ingested_at)                -- partition key must be in PK
) PARTITION BY RANGE (ingested_at);

-- Monthly partitions (auto-created by pg_partman or a migration)
CREATE TABLE events_2026_03 PARTITION OF events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

CREATE TABLE events_2026_04 PARTITION OF events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

-- Indexes (created per partition automatically with partition-wise index)
CREATE INDEX idx_events_device_id      ON events(device_id, ingested_at DESC);
CREATE INDEX idx_events_event_type     ON events(event_type, ingested_at DESC);
CREATE INDEX idx_events_status         ON events(status) WHERE status != 'processed';
CREATE INDEX idx_events_payload_gin    ON events USING GIN(payload);       -- JSONB queries
```

---

### Table: `dlq_events`
```sql
CREATE TABLE dlq_events (
    id              BIGSERIAL   PRIMARY KEY,
    event_id        TEXT        NOT NULL,
    device_id       TEXT,
    original_topic  TEXT        NOT NULL,
    raw_payload     JSONB       NOT NULL,               -- full original Kafka message
    error_message   TEXT        NOT NULL,
    retry_count     INT         NOT NULL DEFAULT 0,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempted  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,                        -- set when replayed + successful
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','replaying','resolved','discarded'))
);

CREATE INDEX idx_dlq_event_id    ON dlq_events(event_id);
CREATE INDEX idx_dlq_device_id   ON dlq_events(device_id);
CREATE INDEX idx_dlq_status      ON dlq_events(status) WHERE status = 'pending';
```

---

### Table: `device_state` (Latest State Cache)
```sql
-- Materialized latest state per device — updated on each processed event
CREATE TABLE device_state (
    device_id       TEXT        PRIMARY KEY REFERENCES devices(device_id),
    last_event_id   TEXT        NOT NULL,
    last_event_type TEXT        NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    last_payload    JSONB       NOT NULL,
    event_count     BIGINT      NOT NULL DEFAULT 1
);
```

---

## 2. Redis Key Schema

| Key Pattern | Type | TTL | Purpose |
|---|---|---|---|
| `iotflow:idempotency:{event_id}` | String | 72h | Seen-event deduplication |
| `iotflow:ratelimit:{device_id}:minute` | String (INCR) | 60s | Per-device per-minute counter |
| `iotflow:ratelimit:{device_id}:hour` | String (INCR) | 3600s | Per-device per-hour counter |
| `iotflow:device:blocked:{device_id}` | String | TTL varies | Temporarily blocked device flag |

---

## 3. Kafka Topic Schema

### Topic: `iot.events.raw`
- **Partition Key**: `device_id` (ensures ordering per device)
- **Message Format**: JSON (Avro with Schema Registry in production)
- **Envelope**:
```json
{
  "kafka_offset": 1048576,
  "kafka_partition": 3,
  "kafka_timestamp_ms": 1742389200000,
  "event": { /* IoTEvent JSON */ }
}
```

### Topic: `iot.events.dlq`
```json
{
  "original_event": { /* IoTEvent JSON */ },
  "original_topic": "iot.events.raw",
  "original_partition": 3,
  "original_offset": 1048576,
  "error": "asyncpg.exceptions.ConnectionDoesNotExistError: ...",
  "retry_count": 5,
  "failed_at": "2026-03-19T14:30:00.000Z"
}
```

---

## 4. Event Schema (JSON Schema Draft 7)
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "IoTEvent",
  "type": "object",
  "required": ["event_id", "device_id", "event_type", "timestamp", "payload"],
  "properties": {
    "event_id":       { "type": "string", "pattern": "^[0-9A-Z]{26}$" },
    "device_id":      { "type": "string", "minLength": 1, "maxLength": 128 },
    "event_type":     { "type": "string", "minLength": 1, "maxLength": 64 },
    "schema_version": { "type": "string", "default": "1.0" },
    "timestamp":      { "type": "string", "format": "date-time" },
    "payload":        { "type": "object" },
    "metadata":       { "type": "object", "default": {} }
  },
  "additionalProperties": false
}
```
