# IoTFlow — Staff Engineer Design Audit (Interview Prep)

This document provides a high-level architectural defense of the design decisions made in IoTFlow. It is structured for Staff+ level technical interviews, focusing on trade-offs and real-world constraints.

---

## 1. Streaming Layer: Kafka vs. NATS JetStream

**Context**: High-scale, durable event log for telemetry.

- **Decision**: **Kafka (KRaft)**
- **Rationale**: IoTFlow requires a durable, rewritable log with a massive ecosystem (Kafka Connect, Flink, Spark) for downstream OLAP. Kafka's "Consumer Group" protocol is more mature for high-scale rebalancing than NATS JetStream's current consumer model.
- **Alternatives Rejected**: 
    - **NATS (Core)**: Rejected due to a lack of durability (fire-and-forget).
    - **NATS JetStream**: Rejected due to smaller ecosystem and more complex configuration for time-series persistence (retention/sharding).
- **Trade-offs**:
    - *Complexity*: Higher (JVM, KRaft tuning).
    - *Latency*: Worse (~10ms) vs NATS (~1ms).
    - *Reliability*: Superior (Leader/Follower replication is battle-tested at petabyte scale).
- **Failure Mode**: Kafka's "Stop the World" rebalances in v1 (non-static) would cause 30s+ gaps in ingestion.
- **Evolution**: At extreme scale (10M+ devices), transition to **Pulsar** for better architectural separation of storage (Bookkeeper) and compute (Broker).

---

## 2. Delivery Guarantees: At-Least-Once vs. Exactly-Once

**Context**: Data integrity for mission-critical sensor readings.

- **Decision**: **At-Least-Once (with Idempotency)**
- **Rationale**: Exactly-once in Kafka (EOS) relies on ACID transactions across producers/partition-leaders, which introduces significant latency (2PC) and reduces maximum throughput by ~30–50%. In IoT, it is cheaper to handle duplicates at the sink than to slow down the source.
- **Alternatives Rejected**: 
    - **Exactly-Once (EOS)**: Rejected due to performance overhead.
    - **At-Most-Once**: Rejected because data loss (skipped events) is unacceptable.
- **Trade-offs**:
    - *Complexity*: Handed off to the application (Idempotency layer).
    - *Reliability*: High (no data loss).
    - *Performance*: Superior (async fire-and-forget).
- **Failure Mode**: When the Idempotency window (72h) closes, old duplicates will be re-processed.
- **Evolution**: Moving to **Transactional PostgreSQL** (using Outbox pattern) for specific "command" events where exactly-once is non-negotiable.

---

## 3. Idempotency Tier: Redis vs. Database Unique Index

**Context**: Deduplicating millions of events per second.

- **Decision**: **Dual-Layer (Redis Fast-Path + DB Safety Net)**
- **Rationale**: Checking a DB index for every event (100K/s) would kill PostgreSQL's IOPS. Redis provides a sub-millisecond "pre-check" that catches 99.9% of duplicates (clocks, network retries) before the expensive DB write occurs.
- **Alternatives Rejected**: 
    - **Database Only**: Rejected due to high write contention and index bloat.
    - **Redis Only**: Rejected due to "Fail-Open" risk (if Redis crashes, you get duplicates).
- **Trade-offs**:
    - *Cost*: Higher ($$$ for Redis RAM).
    - *Complexity*: Requires two-phase logic and cache invalidation.
- **Failure Mode**: "Stale Read" from a Redis replica during failover could cause a double-processing window of <1s.
- **Evolution**: Implement a **Bloom Filter** (RedisBloom) to reduce RAM costs by 90% at the expense of a 0.1% false-positive rate (which the DB then handles).

---

## 4. Partitioning Strategy: `device_id` as Partition Key

**Context**: Ensuring per-device ordering while scaling out.

- **Decision**: **Explicit Partition Key (`device_id`)**
- **Rationale**: IoT events are stateful (delta-encoding, counters). Processing events from the same device in parallel across different pods leads to race conditions in the DB state. `device_id` ensures all events for Device A hit Partition X and are processed by Consumer Y.
- **Alternatives Rejected**: 
    - **Round-Robin**: Rejected because it breaks ordering.
    - **Event ID Hashing**: Rejected because it breaks ordering.
- **Trade-offs**:
    - *Downside*: **Hot Partitions**. If one device (e.g., a gateway) sends 1000x more traffic than others, that one partition/consumer will bottleneck.
- **Failure Mode**: A "whale" device saturating a single Kafka partition.
- **Evolution**: Implement **Hierarchical Partitioning** or "Sub-keys" (e.g., `device_id:batch_id`) if single-device throughput exceeds single-core processing capacity (~5K/s).

---

## 5. Processing Model: Synchronous vs. Asynchronous Ingestion

**Context**: Managing backpressure at the MQTT border.

- **Decision**: **Asynchronous (Internal Buffer)**
- **Rationale**: In v2, the `Ingestion` service uses an `asyncio.Queue` to decouple the MQTT receive loop from the Kafka produce loop. This allows the system to absorb micro-bursts without blocking the MQTT socket.
- **Alternatives Rejected**: 
    - **Sync Block**: Rejected because a slow Kafka broker would block the MQTT broker's network thread, causing device disconnects.
- **Trade-offs**:
    - *Risk*: Data loss on pod crash (events in the queue).
- **Failure Mode**: Sudden pod OOM kills 10,000 "queued" messages.
- **Evolution**: Replace the internal memory queue with a **Local Persistent Volume (EBS)** and use an "at-least-once" handoff between MQTT and the local write-ahead log (WAL).

---

## Summary of Architectural Posture

| Decision | Primary Driver | Rejected Strategy |
|---|---|---|
| **Kafka** | Ecosystem / Durability | NATS (Messaging first) |
| **At-Least-Once** | Performance | EOS (Latency first) |
| **Dual-Layer Dedup** | DB Health | DB-Only (Contention) |
| **Device Ordering** | Data Correctness | Round-Robin (Throughput first) |
| **Async Queues** | System Stability | Sync Block (Backpressure) |
