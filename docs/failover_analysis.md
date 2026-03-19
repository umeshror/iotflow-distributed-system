# IoTFlow — Stress Test & Failure Analysis

This document acts as a Staff+ level audit of the IoTFlow system, identifying 15 critical failure scenarios and proposing robust, production-grade fixes.

## 1. Failure Scenarios & Robust Fixes

| # | Scenario | What Goes Wrong | Current Behavior | Robust Staff+ Fix |
|---|---|---|---|---|
| 1 | **MQTT Broker Network Partition** | Broker cluster splits; devices connect to different "islands". | Local baseline (Mosquitto) would lose availability. | **EMQX Clustering + Consistent Hash LB**: Use an NLB with sticky sessions or consistent hashing at the broker level to ensure "Split Brain" is handled by the cluster's internal state sync (via Mnesia/Raft). |
| 2 | **Duplicate MQTT Message (QoS 1)** | Network ack lost; device sends message again with same `event_id`. | `ingestion` service produces it to Kafka twice. | **Kafka Idempotent Producer**: Already enabled (`enable_idempotence=True`). Kafka drops the duplicate at the broker side if the internal sequence ID matches. |
| 3 | **Out-of-Order Events** | Network jitter causes $t_2$ to arrive before $t_1$. | System persists $t_2$, then $t_1$. Device state might move "backwards". | **State Upsert Guard**: Change `upsert_device_state` to only update if `last_seen_at < EXCLUDED.last_seen_at`. Never trust ingestion order for state. |
| 4 | **Kafka Broker Downtime** | Kafka cluster is unreachable or in a leader-election loop. | `ingestion` service buffers in `_MSG_QUEUE` (10K limit), then starts dropping MQTT messages. | **Local Spillover Storage**: Implement "Sidecar Side-channel" or local disk buffering (BoltDB/RocksDB) for ingestion pods to survive >10m of Kafka downtime without dropping data. |
| 5 | **Kafka Rebalance Storm** | Frequent pod scaling causes constant "stop-the-world" rebalances. | Processing pauses for ~5–30s every time a pod joins/leaves. | **Static Membership**: Use Kafka 2.3+ `group.instance.id` to allow pods to restart/scale without triggering a rebalance if they return within the `session.timeout.ms`. |
| 6 | **Kafka Consumer Lag Spike** | Traffic burst exceeds processing capacity. | Lag grows; real-time visibility is lost. | **KEDA + Fine-grained Scaling**: Scale workers based on `lag / partitions`. v2 upgrade uses `asyncio.Semaphore(100)` to ensure one pod can max-out a partition. |
| 7 | **Redis Idempotency Failure** | Redis is down or returns an error. | System fails-open; defaults to "True" (process it). | **Two-Phase Dedup**: Maintain a local (in-memory) LRU cache of recently processed IDs to catch ~90% of duplicates before even hitting Redis. |
| 8 | **Redis Key Eviction** | Redis runs out of memory; evicts idempotency keys early. | A duplicate arriving after eviction will be processed again. | **PostgreSQL PK Safety Net**: Ensure `UNIQUE(event_id)` is enforced at DB level. Already implemented via `ON CONFLICT DO NOTHING`. |
| 9 | **PostgreSQL Primary Failure** | DB is in failover; cluster is RO for 30–60s. | `worker` pods retry 5× then publish to DLQ. | **DLQ Buffer & Replay**: This is the correct behavior. Ensure DLQ records are easily "replayable" once the DB is healthy. |
| 10 | **Poison Message** | A malformed event causes the worker code to crash or hang. | Message returns to head of queue; worker crashes again (Infinite loop). | **Negative Ack + Retries Check**: If `retry_count > MAX`, send to DLQ immediately. Current code catches `ValidationError` and sends to DLQ — good. |
| 11 | **DLQ Publish Failure** | Kafka AND PostgreSQL are both failing for a DLQ write. | The message is lost permanently (Madness scenario). | **Local Log Spillover**: Write the raw event to a structured error log file on disk. Use a "log-shipper" (FluentBit) to send it to S3/Loki for manual rescue. |
| 12 | **Schema Evolution Mismatch** | New device sends V2 payload; old Worker code can't parse it. | Worker sends to DLQ as `VALIDATION_ERROR`. | **Schema Registry**: Use Confluent Schema Registry (Avro/Protobuf) to ensure forward/backward compatibility before the event even enters Kafka. |
| 13 | **Redis Rate Limit "Stutter"** | Redis network jitter causes rate limit check to take >100ms. | Ingestion dispatcher blocks; internal queue fills up. | **Async Decoupled Limiting**: Make rate limiting "asynchronous-local". Ingestion fetches quota in batches (e.g., 100 permits) and enforces locally. |
| 14 | **Systemic Clock Drift** | Device clock is wrong (e.g., 2030-01-01). | Event is persisted far in the "future" or "past", breaking TTL/Partitions. | **Wall-clock Sanity Check**: Ingestion service should reject any event with `timestamp` $> \pm 1$ hour from `NOW()`. |
| 15 | **Re-sync / Backfill Hammer** | Replaying 10M events from DLQ kills the production DB. | Backfill traffic starves live device traffic. | **Weighted Queues / Sharding**: Use a separate "Replay Worker" pool with a lower `DB_POOL_MAX_SIZE` to ensure backfills are throttled relative to live traffic. |

---

## 2. Strategic Improvements

### Retry Strategy 2.0
- **Problem**: Uniform exponential backoff can still cause "pulses" of load.
- **Fix**: **Full Jitter**. Instead of `base * multiplier^attempt`, use `random(0, backoff)`. This distributes the retries evenly over the entire window.

### DLQ Handling & "Circuit Breaker"
- **Problem**: If the system is sending 100% of messages to DLQ (e.g., DB down), the DLQ topic itself might become a bottleneck.
- **Fix**: **Circuit Breakers**. If DLQ rate exceeds 10% of total traffic, the `worker` should pause consumption (backpressure) instead of dumping "everything" into the DLQ.

### Backpressure Strategy
- **Layer 1 (Ingestion)**: Bounded `asyncio.Queue`. When full, we **stop reading from MQTT**. This forces the MQTT broker to buffer (or the device to retry).
- **Layer 2 (Worker)**: `asyncio.Semaphore(100)`. Limits concurrent DB writes. This is "Natural Backpressure" — Kafka lag grows, KEDA scales up, but no single DB node is killed.

---

## 3. Top Weak Points & Trade-offs

1.  **Weak Point: Single-Region Postgres**.
    - *Risk*: A whole cloud region outage kills the system.
    - *Trade-off*: Multi-region Postgres (Aurora Global / CockroachDB) adds significant latency and cost ($$$). Baseline uses DLQ + S3 archival as a cheaper "recovery-only" path.
2.  **Weak Point: Redis Idempotency TTL**.
    - *Risk*: 72h window means duplicates older than 3 days will be re-processed.
    - *Trade-off*: Infinite idempotency storage in Redis is too expensive. We rely on the DB's `event_id` unique index for the "permanent" safety net.
3.  **Weak Point: In-memory Queue in Ingestion**.
    - *Risk*: If the pod crashes, 10,000 "accepted" MQTT messages are lost.
    - *Trade-off*: Using a local disk-backed queue (NATS JetStream / Persistent Volume) increases latency and cloud cost (EBS IOPS). QoS 1 MQTT handles this by the device retrying if the broker doesn't ack back.

---

## 4. Final Verdict for Staff+ Interview
The system is **Resilient** but not yet **Impenetrable**. The move from v1 (serial) to v2 (batched/pipelined) removed the throughput bottlenecks, but introduced "Batch Failure" risks. The next step in maturation is **Observability-driven Auto-remediation** (e.g., auto-replaying DLQ when DB metrics level out).
