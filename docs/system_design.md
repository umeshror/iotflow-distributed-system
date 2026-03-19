# IoTFlow — System Architecture

## 1. High-Level Architecture

IoTFlow ingests events from millions of IoT devices over MQTT, streams them via Kafka, processes them asynchronously with stateless workers, enforces idempotency via Redis, and persists canonical device state and event history to PostgreSQL. A Dead Letter Queue (DLQ) captures all unprocessable events for offline analysis and replay.

```mermaid
graph TB
    subgraph Devices["IoT Devices (Millions)"]
        D1[Sensor A]
        D2[Sensor B]
        D3[Gateway]
    end

    subgraph Ingestion["Ingestion Layer"]
        MB[MQTT Broker\nMosquitto / EMQ X]
        IS[Ingestion Service\nFastAPI + aiomqtt]
    end

    subgraph Streaming["Streaming Layer"]
        K[Kafka Cluster\n3+ Brokers]
        KT_RAW[Topic: iot.events.raw]
        KT_VALID[Topic: iot.events.validated]
        KT_DLQ[Topic: iot.events.dlq]
    end

    subgraph Processing["Processing Layer"]
        W1[Worker Pod 1]
        W2[Worker Pod 2]
        WN[Worker Pod N]
    end

    subgraph State["State & Cache"]
        R[Redis Cluster\nIdempotency + Rate Limit]
        PG[PostgreSQL\nPrimary + Replica]
    end

    subgraph Observability["Observability"]
        PROM[Prometheus]
        GRAF[Grafana]
        LOKI[Loki / ELK]
    end

    D1 & D2 & D3 -->|MQTT TLS| MB
    MB -->|subscribe| IS
    IS -->|validate + produce| KT_RAW
    KT_RAW --> W1 & W2 & WN
    W1 & W2 & WN -->|idempotency check| R
    W1 & W2 & WN -->|persist| PG
    W1 & W2 & WN -->|failed events| KT_DLQ
    W1 & W2 & WN -->|emit metrics| PROM
    IS -->|emit metrics| PROM
    PROM --> GRAF
    IS & W1 -->|structured logs| LOKI
```

---

## 2. Component Breakdown

### 2.1 MQTT Broker (EMQ X / Mosquitto)
| Attribute | Detail |
|---|---|
| Role | Protocol gateway for device connectivity |
| Protocol | MQTT 3.1.1 / 5.0 over TLS |
| Auth | mTLS + JWT-based username/password |
| QoS | QoS 1 (at-least-once) from device→broker |
| Clustering | EMQX horizontal cluster via Raft |
| Backpressure | `max_inflight_messages`, per-client rate limiting |

### 2.2 Ingestion Service
| Attribute | Detail |
|---|---|
| Runtime | Python 3.12, FastAPI (async), `aiomqtt` |
| Responsibility | Subscribe all MQTT topics → validate schema → produce to Kafka |
| Rate Limiting | Token bucket per `device_id` in Redis (Lua script) |
| Schema Validation | JSON Schema (Draft 7) via `jsonschema` + Pydantic |
| Scaling | Stateless — scale horizontally behind a load balancer |
| Health | `/healthz` (liveness), `/readyz` (readiness) |

### 2.3 Kafka Cluster
| Attribute | Detail |
|---|---|
| Role | Durable, ordered, replayable event log |
| Topics | `iot.events.raw`, `iot.events.validated`, `iot.events.dlq` |
| Partitioning | Partitioned by `device_id` (ensures per-device ordering) |
| Replication | `replication.factor=3`, `min.insync.replicas=2` |
| Retention | `iot.events.raw` = 7 days, DLQ = 30 days |
| Producer Acks | `acks=all` — strongest durability guarantee |

### 2.4 Worker Service
| Attribute | Detail |
|---|---|
| Role | Consume validated events, apply business logic, persist |
| Consumer Group | `iotflow-workers` — enables parallel consumption |
| Offset Management | Manual commit after successful DB write |
| Idempotency | Redis `SET NX EX` with `event_id` key before DB write |
| Retry | Exponential backoff up to 5 attempts, then DLQ |
| Processing | Async I/O — `asyncpg` for PG, `redis.asyncio` |

### 2.5 Redis Cluster
| Attribute | Detail |
|---|---|
| Role | Idempotency store + rate limiter |
| Idempotency TTL | 72 hours (covers retry windows) |
| Rate Limit | Sliding window counter per device (Lua atomic) |
| Cluster Mode | Redis Cluster (3 shards × 2 replicas) |
| Failure Mode | Degrade gracefully — skip Redis check under failure, accept duplicates |

### 2.6 PostgreSQL
| Attribute | Detail |
|---|---|
| Role | Source of truth — device registry, processed events, device state |
| HA Setup | Primary + 2 streaming replicas (Patroni / RDS Multi-AZ) |
| Connection Pool | PgBouncer (transaction mode, pool size 100) |
| Partitioning | `events` table partitioned by `created_at` (monthly) |
| Writes | Workers write directly to primary |
| Reads | API / dashboards read from replicas |

### 2.7 Dead Letter Queue (DLQ)
| Attribute | Detail |
|---|---|
| Implementation | Kafka topic `iot.events.dlq` |
| Trigger | Event fails all retry attempts OR fails schema validation with no fix path |
| Message Envelope | Original payload + error reason + retry count + timestamp |
| Replay | Manual or automated via DLQ-replay service (re-publishes to `iot.events.raw`) |
| Alerting | Prometheus alert if DLQ message rate > threshold |

### 2.8 Observability Stack
| Component | Tool | Purpose |
|---|---|---|
| Metrics | Prometheus + Grafana | Latency, throughput, error rates, lag |
| Logging | Structured JSON → Loki / ELK | Correlation by `event_id`, `device_id`, `trace_id` |
| Tracing | OpenTelemetry (optional phase 2) | End-to-end request traces |
| Alerting | Grafana Alerting / PagerDuty | SLA breaches, DLQ spikes, consumer lag |

---

## 3. Sequence Diagrams

### 3.1 Happy Path — Device Event Ingestion

```mermaid
sequenceDiagram
    participant D as IoT Device
    participant MB as MQTT Broker
    participant IS as Ingestion Service
    participant R as Redis
    participant K as Kafka
    participant W as Worker
    participant PG as PostgreSQL

    D->>MB: PUBLISH /devices/{id}/events (QoS 1)
    MB-->>D: PUBACK
    MB->>IS: Forward message (aiomqtt subscription)
    IS->>IS: Validate JSON Schema
    IS->>R: INCR rate_limit:{device_id} / check threshold
    R-->>IS: OK (within limit)
    IS->>K: produce(topic=iot.events.raw, key=device_id)
    K-->>IS: ACK (acks=all)
    K->>W: consume(iot.events.raw)
    W->>R: SET NX EX 72h iot:idempotency:{event_id}
    R-->>W: OK (not seen before)
    W->>PG: INSERT INTO events (...) ON CONFLICT DO NOTHING
    PG-->>W: OK
    W->>K: manual commit offset
```

### 3.2 Retry + DLQ Flow

```mermaid
sequenceDiagram
    participant K as Kafka
    participant W as Worker
    participant R as Redis
    participant PG as PostgreSQL
    participant DLQ as DLQ Topic

    K->>W: consume event
    W->>PG: INSERT → FAIL (DB down)
    W->>W: backoff(attempt=1, delay=1s)
    W->>PG: retry → FAIL
    W->>W: backoff(attempt=2, delay=2s)
    W->>PG: retry → FAIL
    W->>W: backoff(attempt=3, delay=4s)
    W->>PG: retry → FAIL
    W->>W: backoff(attempt=4, delay=8s)
    W->>PG: retry → FAIL (attempt 5)
    W->>DLQ: produce(iot.events.dlq, {payload, error, attempts=5})
    W->>K: commit offset (do not block pipeline)
```

### 3.3 Idempotency — Duplicate Detection

```mermaid
sequenceDiagram
    participant W as Worker
    participant R as Redis
    participant PG as PostgreSQL

    note over W: Event received (possibly duplicate due to QoS 1 re-delivery)
    W->>R: SET NX EX 259200 iot:idempotency:{event_id}
    alt Not seen before
        R-->>W: 1 (key set)
        W->>PG: INSERT event
    else Already processed
        R-->>W: 0 (key exists)
        W->>W: discard — log as duplicate
        W->>K: commit offset
    end
```

---

## 4. Failure Scenarios

### 4.1 MQTT Broker Down
- **Impact**: Devices cannot publish. MQTT clients buffer messages per QoS level.
- **Mitigation**: EMQ X cluster with 3+ nodes; Kubernetes liveness probes restart unhealthy pods. Device SDKs retry connection with exponential backoff. Events are stored locally on devices (edge buffering) if supported.
- **Recovery**: Devices reconnect automatically (persistent sessions, QoS 1 re-deliver buffered messages).

### 4.2 Kafka Down
- **Impact**: Ingestion service cannot produce. Events are lost unless buffered.
- **Mitigation**: Ingestion service uses an in-memory or disk-backed local buffer (bounded queue, e.g., 10K events). Producer retries with `delivery.timeout.ms=120000`. Multi-broker Kafka cluster (3 brokers) tolerates 1 broker failure.
- **Recovery**: Kafka recovers in-ISR replicas; buffered events are flushed on reconnect.

### 4.3 Redis Failure
- **Impact**: Idempotency checks unavailable; rate limiting unavailable.
- **Mitigation**: Workers degrade gracefully — skip Redis check, fall back to DB-level `ON CONFLICT DO NOTHING`. Rate limiting skipped temporarily. Alert fires for manual review.
- **Recovery**: Redis Cluster auto-promotes replicas (~tens of seconds). Short window of duplicate acceptance is acceptable vs. blocking entire pipeline.

### 4.4 PostgreSQL Downtime
- **Impact**: Workers cannot persist events.
- **Mitigation**: Workers retry with exponential backoff (up to 5 attempts). Events remain in Kafka (7-day retention) — no data is lost. After max retries, events go to DLQ for replay after DB recovers.
- **Recovery**: Patroni auto-failover to replica (~30s). DLQ replay re-injects events.

### 4.5 Duplicate Messages
- **Cause**: MQTT QoS 1 re-delivery, Kafka producer retries, worker crashes after write but before offset commit.
- **Mitigation**: Tri-layer idempotency — Redis NX check, DB `ON CONFLICT DO NOTHING`, unique constraint on `event_id` column.

### 4.6 Slow Consumers
- **Impact**: Kafka consumer lag grows, memory pressure, processing delay.
- **Mitigation**: Horizontal scaling of worker pods (HPA in Kubernetes on consumer lag metric via KEDA). Backpressure: workers have bounded internal task queues (`asyncio.Semaphore`). Circuit breaker pattern on DB connections.
