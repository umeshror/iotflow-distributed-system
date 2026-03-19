# IoTFlow — Retry, DLQ & Idempotency Design

## 1. Retry Strategy

### Policy
| Parameter | Value | Rationale |
|---|---|---|
| Max attempts | 5 | Balances durability with pipeline throughput |
| Base delay | 1 second | Gives transient errors time to recover |
| Multiplier | 2× (exponential) | Standard exponential backoff |
| Max delay cap | 30 seconds | Prevents excessive lag accumulation |
| Jitter | ±25% random | Avoids thundering herd on shared resources |

### Backoff Schedule (per event, without jitter)
| Attempt | Delay Before Retry |
|---|---|
| 1 | 1s |
| 2 | 2s |
| 3 | 4s |
| 4 | 8s |
| 5 | 16s (then → DLQ) |
| **Total window** | **~31s** |

### Retry Scope
Only **transient** errors are retried:
- DB connection errors
- Redis connection errors
- Network timeouts
- Kafka produce timeout (DLQ publish itself)

**Non-retryable** errors go directly to DLQ:
- JSON Schema validation failure
- Unknown `device_id` (device not registered)
- Payload exceeds size limit

---

## 2. Dead Letter Queue Design

### Architecture
```
Kafka Topic: iot.events.dlq
  Partitions: 6
  Replication: 3
  Retention: 30 days
  Cleanup policy: delete
```

### DLQ Message Envelope
```json
{
  "dlq_id": "01HXYZ...",
  "original_event": { /* full original IoTEvent */ },
  "original_topic": "iot.events.raw",
  "original_partition": 3,
  "original_offset": 104857601,
  "error_type": "DB_CONNECTION_ERROR",
  "error_message": "asyncpg: connection timeout after 5s",
  "retry_count": 5,
  "failed_at": "2026-03-19T14:30:00.000Z",
  "worker_id": "worker-pod-2"
}
```

### DLQ Flow
```
Normal path:          Kafka → Worker → DB
                                    ↓ (5 × retry fail)
DLQ publish:          Worker → iot.events.dlq
DLQ record:           Worker → dlq_events table (PostgreSQL)
Alert:                Prometheus fires alert if DLQ rate > 10/min
Manual review:        Ops team inspects dlq_events table
Replay:               POST /v1/dlq/events/{id}/replay
                        → re-publishes to iot.events.raw
                        → sets dlq_events.status = 'replaying'
```

### Replay Safety
- Replayed events carry the **same `event_id`** — idempotency check will deduplicate if already processed.
- DLQ record marked `resolved` only after successful reprocessing.
- Replay rate-limited to avoid flooding normal pipeline.

---

## 3. Idempotency Strategy

### Problem
MQTT QoS 1 guarantees **at-least-once** delivery. Kafka producer retries and worker crashes after write-before-commit create further duplicate risks.

### Tri-Layer Defense

#### Layer 1 — Redis `SET NX`
```python
# Worker checks before any processing
key = f"iotflow:idempotency:{event.event_id}"
result = await redis.set(key, "1", nx=True, ex=259200)  # 72h TTL
if result is None:
    # Key already existed → duplicate, skip
    logger.info("Duplicate event skipped", event_id=event.event_id)
    return
```
- **Fast path**: O(1) Redis check
- **TTL**: 72h covers retry + DLQ replay windows
- **Failure mode**: If Redis is down, fall through to Layer 2

#### Layer 2 — PostgreSQL Unique Constraint
```sql
-- The unique index on event_id makes duplicates a NO-OP
INSERT INTO events (event_id, device_id, ...)
VALUES ($1, $2, ...)
ON CONFLICT (event_id) DO NOTHING;
```
- **Safety net**: Even if Redis misses, DB prevents duplicate rows
- **Note**: `ON CONFLICT DO NOTHING` returns 0 rows affected — worker checks this and avoids double-counting metrics

#### Layer 3 — Kafka Offset Tracking
- Worker uses **manual offset commit** — only commits after successful DB write
- On crash/restart, Kafka re-delivers uncommitted events → caught by Layer 1 or 2

### Idempotency Window
| Layer | Window | Mechanism |
|---|---|---|
| Redis | 72 hours | TTL-based key expiry |
| PostgreSQL | Permanent | Unique index on `event_id` |
| Kafka | 7 days (raw topic retention) | Offset rewind for replay |

---

## 4. Rate Limiting

### Algorithm: Sliding Window with Redis Lua

```lua
-- Atomic: GET + conditional INCR + EXPIRE in single round trip
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
  redis.call('EXPIRE', key, window)
end
if current > limit then
  return 0   -- rate limited
end
return 1     -- allowed
```

### Limits
| Tier | Limit | Window |
|---|---|---|
| Per-device (burst) | 100 events | 1 minute |
| Per-device (sustained) | 5000 events | 1 hour |
| Per-topic (aggregate) | 500K events | 1 minute |

### Rate Limit Response
When exceeded: `HTTP 429` with `Retry-After` header. MQTT: `DISCONNECT` packet with reason code `0x96` (Message rate too high) — MQTT 5.0 only.

---

## 5. Backpressure

### Ingestion → Kafka
- Ingestion uses bounded `asyncio.Queue` (size: 10K). If full, MQTT messages are NACK'd (QoS 1 → device will retry).
- Kafka producer uses `max_block_ms=5000` — if Kafka is full/slow, producer blocks and triggers upstream NACK.

### Kafka → Workers
- Workers use `asyncio.Semaphore(max_concurrent=50)` — limits in-flight DB writes.
- KEDA (Kubernetes Event-Driven Autoscaler) scales worker replicas based on consumer group lag metric from Kafka Exporter.

```yaml
# KEDA ScaledObject
triggers:
  - type: kafka
    metadata:
      bootstrapServers: kafka:9092
      consumerGroup: iotflow-workers
      topic: iot.events.raw
      lagThreshold: "1000"    # Scale up if lag > 1000 messages per worker
      activationLagThreshold: "100"
```
