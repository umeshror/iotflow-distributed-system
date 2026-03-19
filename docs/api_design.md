# IoTFlow — API Design

## 1. MQTT Topic Contract (Device → Broker)

### Publish Topic
```
devices/{device_id}/events
devices/{device_id}/heartbeat
```

### Subscribe Topics (Server → Device)
```
devices/{device_id}/commands
devices/{device_id}/config
```

### Event Payload (JSON)
```json
{
  "event_id": "01HXYZ9K2PNMQ3RT4VW5AB6CD7",   // ULID — sortable, unique
  "device_id": "sensor-plant-floor-022",
  "event_type": "temperature_reading",
  "schema_version": "1.0",
  "timestamp": "2026-03-19T14:00:00.000Z",        // ISO 8601 UTC
  "payload": {
    "value": 72.4,
    "unit": "fahrenheit",
    "location": "warehouse-b-zone-3"
  },
  "metadata": {
    "firmware": "2.1.3",
    "signal_strength_dbm": -68
  }
}
```

---

## 2. Ingestion Service REST API

Base URL: `http://ingestion-service:8000`

### Health / Readiness

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe — returns 200 if process alive |
| `GET` | `/readyz` | Readiness probe — checks MQTT + Kafka connectivity |

**`GET /readyz` Response (200 OK):**
```json
{
  "status": "ready",
  "checks": {
    "mqtt": "ok",
    "kafka": "ok",
    "redis": "ok"
  }
}
```

**`GET /readyz` Response (503 — degraded):**
```json
{
  "status": "degraded",
  "checks": {
    "mqtt": "ok",
    "kafka": "error: connection refused",
    "redis": "ok"
  }
}
```

---

### Device Management (REST)

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/devices` | Register a new device |
| `GET` | `/v1/devices/{device_id}` | Get device metadata |
| `DELETE` | `/v1/devices/{device_id}` | Deregister device |

**`POST /v1/devices` Request:**
```json
{
  "device_id": "sensor-plant-floor-022",
  "device_type": "temperature_sensor",
  "location": "warehouse-b-zone-3",
  "firmware_version": "2.1.3",
  "tags": ["production", "critical"]
}
```

**`POST /v1/devices` Response (201 Created):**
```json
{
  "device_id": "sensor-plant-floor-022",
  "status": "active",
  "registered_at": "2026-03-19T14:00:00Z",
  "mqtt_credentials": {
    "username": "sensor-plant-floor-022",
    "password_hash": "bcrypt:$2b$12$..."
  }
}
```

---

### Event Query API

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/events` | Query events (paginated) |
| `GET` | `/v1/events/{event_id}` | Get specific event |
| `GET` | `/v1/devices/{device_id}/events` | Events for one device |

**`GET /v1/events` Query Params:**
| Param | Type | Description |
|---|---|---|
| `device_id` | string | Filter by device |
| `event_type` | string | Filter by type |
| `from` | ISO8601 | Start of time range |
| `to` | ISO8601 | End of time range |
| `limit` | int (1–1000) | Page size, default 100 |
| `cursor` | string | Opaque pagination cursor |

**`GET /v1/events` Response (200 OK):**
```json
{
  "data": [
    {
      "event_id": "01HXYZ9K2PNMQ3RT4VW5AB6CD7",
      "device_id": "sensor-plant-floor-022",
      "event_type": "temperature_reading",
      "payload": {"value": 72.4, "unit": "fahrenheit"},
      "processed_at": "2026-03-19T14:00:00.123Z",
      "status": "processed"
    }
  ],
  "pagination": {
    "next_cursor": "eyJpZCI6IjAxSFhZWiIsInRzIjoiMjAyNiJ9",
    "has_more": true,
    "total_count": 48320
  }
}
```

---

### DLQ Management API

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/dlq/events` | List DLQ events |
| `POST` | `/v1/dlq/events/{event_id}/replay` | Replay a DLQ event |
| `DELETE` | `/v1/dlq/events/{event_id}` | Discard a DLQ event |

**`GET /v1/dlq/events` Response:**
```json
{
  "data": [
    {
      "event_id": "01HXYZ...",
      "original_topic": "iot.events.raw",
      "error": "DB connection timeout after 5 retries",
      "retry_count": 5,
      "first_seen": "2026-03-19T13:00:00Z",
      "last_attempted": "2026-03-19T13:02:10Z"
    }
  ]
}
```

---

## 3. Error Codes

| HTTP Status | Code | Meaning |
|---|---|---|
| 400 | `INVALID_SCHEMA` | Payload fails JSON Schema validation |
| 400 | `MISSING_FIELD` | Required field absent |
| 404 | `DEVICE_NOT_FOUND` | Unknown device_id |
| 409 | `DEVICE_EXISTS` | Device already registered |
| 422 | `INVALID_TIMESTAMP` | Timestamp not ISO 8601 UTC |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many events from device |
| 503 | `STREAM_UNAVAILABLE` | Kafka/NATS not reachable |

**Error Response Shape:**
```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Device sensor-022 exceeded 100 events/minute limit",
    "device_id": "sensor-022",
    "retry_after_seconds": 45
  }
}
```
