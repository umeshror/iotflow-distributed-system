# IoTFlow: Distributed Event Ingestion & Processing

> **A production-ready system designed for unreliable IoT environments.**
> Built for **100K+ events/sec** with strong idempotency, failure handling, and multi-AZ resilience.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-green.svg)](https://www.python.org/)
[![Docker Compose](https://img.shields.io/badge/docker--compose-ready-blue.svg)](docker-compose.yml)

---

## 🚀 The Reliability Paradox in IoT

In industrial IoT (factories, energy grids, logistics), devices operate in "harsh" network environments. Connections are intermittent, but the data is mission-critical. A 10ms temperature spike or a pressure drop in a gas pipeline cannot be missed, duplicated, or processed out of order.

**IoTFlow** is a distributed event backbone that solves the three core challenges of IoT at scale:
1.  **Ingestion Reliability**: Handling millions of concurrent MQTT connections with backpressure.
2.  **Data Integrity**: Guaranteeing "at-least-once" delivery with "exactly-once" processing (idempotency).
3.  **Operational Resilience**: Surviving partial infrastructure failures (Kafka/DB/Redis) without data loss.

---

## 🏗️ High-Scale v2 Architecture (100K msg/s)

This system is designed around a **decoupled, event-driven architecture** optimized for throughput and linear horizontal scaling.

```mermaid
graph TB
    subgraph Devices["IoT Devices (5M+)"]
        D[Sensors]
    end

    subgraph LB["Load Balancer (NLB)"]
        NLB[MQTT LB\nConsistent Hash]
    end

    subgraph Ingestion["Ingestion Service (FastAPI)"]
        IS["16 Dispatchers/Pod\nBatched Kafka Produce\nLocal Rate-Limit Cache"]
    end

    subgraph Streaming["Kafka Cluster (v2)"]
        K2["48 Partitions\n5 Brokers\nReplication=3"]
    end

    subgraph Workers["Worker Service (asyncio)"]
        W["KEDA Scaled (48 pods)\nSemaphore Backpressure\nPipelined Idempotency"]
    end

    subgraph State["State Tier"]
        RC[Redis Cluster\n12 Shards]
        PG[PostgreSQL\n4 Shards (BRIN)]
    end

    D -->|MQTT QoS1| NLB
    NLB --> IS
    IS -->|Batch| K2
    K2 --> W
    W -->|Pipeline| RC
    W -->|Sharded| PG
```

### **Core Design Decisions**
| Decision | Rationale | Staff Engineer Insight |
|---|---|---|
| **Kafka vs NATS** | Kafka's durable log + ecosystem. | High-scale rebalancing is more mature in Kafka; NATS is better for Command & Control. |
| **At-Least-Once** | Performance priority. | Handling duplicates at the sink (Worker) is cheaper than transactional produce. |
| **Dual-Layer Dedup** | DB Health Protection. | Redis provides a sub-ms "Fast-Path" before the expensive sharded DB write. |
| **Batch Flush** | 20x Produce Throughput. | Decoupling MQTT receive from Kafka ACK amortizes broker RTT across 200+ events. |

---

## 📈 Capacity & Scaling (v2)

IoTFlow is architected for **linear horizontal scaling**. Adding nodes to any tier increases performance predictably.

| Tier | Baseline (4.2K/s) | High-Scale (100K/s) | Scale trigger |
|---|---|---|---|
| **Ingestion** | 3 pods | 40 pods | CPU > 60% (HPA) |
| **Workers** | 7 pods | 48–128 pods | Kafka Lag > 500 (KEDA) |
| **Kafka** | 12 partitions | 48–64 partitions | Throughput > 80% |
| **Redis** | 3 shards | 12 shards | Memory > 70% |
| **PostgreSQL** | 1 primary | 4–6 sharded primaries | Write latency p99 > 50ms |

- **Storage Growth**: ~4.3 TB/day raw telemetry at 50K msg/s.
- **Memory**: ~1.1 TB Redis RAM required for 72h idempotency window.
- **Network**: ~1.2 Gbps internal internal bandwidth optimized via batching.

Full Capacity Planning → [`docs/capacity_planning.md`](docs/capacity_planning.md)

---

## 🛡️ Failure Handling & Audit

How the system handles "The Worst Day" scenarios (15 scenarios audited).

| Scenario | Behavior | Robust Staff+ Fix |
|---|---|---|
| **Poison Message** | Validated at Ingestion/Worker. | Sent to DLQ immediately; no infinite retry. |
| **Kafka Downtime** | 10K internal buffer. | Spillover to local persistent storage (RocksDB sidecar). |
| **Redis Down** | Fail-Open (Skip dedup). | DB `ON CONFLICT` safety net catches all duplicates. |
| **DB Failover** | Retry 5x → DLQ. | Circuit Breaker: Pause ingestion core if DLQ > 10% rate. |
| **Out-of-Order** | Best-effort. | `last_seen_at` guard in SQL state upsert. |

Full Failure Analysis → [`docs/failover_analysis.md`](docs/failover_analysis.md)

---

## 🛠️ How to Run Locally

### 1. Requirements
- Docker 24+ & Compose V2
- `mosquitto_pub` (for simulation)

### 2. Startup
```bash
cp .env.example .env
docker compose up -d
```
Wait ~30s for healthchecks. `docker compose ps` should show all services as `(healthy)`.

### 3. Simulate High-Scale Event
```bash
mosquitto_pub -h localhost -p 1883 -t "devices/sensor-001/events" \
  -m '{"event_id":"01HXYZABCDEFGHJKMNPQRSTVWX","device_id":"sensor-001","event_type":"temp","timestamp":"2026-03-19T14:00:00Z","payload":{"value":72.4}}'
```

### 4. Verify
- **Log monitoring**: `docker compose logs -f worker`
- **DB Check**: `docker compose exec postgres psql -U iotflow -c "SELECT * FROM events LIMIT 5;"`
- **Metrics**: Open `http://localhost:3000` (Grafana) | `http://localhost:9090` (Prometheus)

---

## 📖 System Design Deep-Dive

Browse the core documentation for a deep-dive into each layer.

- [**Design Decisions Audit**](docs/design_decisions.md): Detailed trade-offs and rationale.
- [**High-Scale Upgrade**](docs/high_scale_upgrade.md): The math and architecture behind 100K msg/s.
- [**Failover Analysis**](docs/failover_analysis.md): 15 failure scenarios and mitigations.
- [**Capacity Planning**](docs/capacity_planning.md): Hardware sizing and storage growth.
- [**API & Data Model**](docs/api_design.md): Payloads, topics, and schemas.

---

## License
MIT — See [LICENSE](LICENSE).
