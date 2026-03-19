# IoTFlow — Capacity Planning (50K events/sec)

This report outlines the infrastructure requirements to support a high-scale IoT deployment of **500,000 devices** producing **50,000 events/sec** peak, with **1KB payloads**.

## 1. Capacity Calculations (The Raw Numbers)

| Metric | Calculation | Result |
|---|---|---|
| **Network (Ingress)** | 50,000 msg/s × 1KB payload | **50 MB/s (~400 Mbps)** |
| **Kafka Internal BW** | 50 MB/s × 3 (replication-factor) | **150 MB/s (~1.2 Gbps)** |
| **Storage (Kafka - 7d)** | 50 MB/s × 86,400s × 7d × 3 (rep) | **~90.7 TB** |
| **Storage (DB - monthly)** | 50 MB/s × 86,400s × 30d | **~129.6 TB** (before indexes/WAL) |
| **Redis Memory (72h idemp)** | 35K avg msg/s × 259,200s × 128B key | **~1.1 TB** |
| **DB Write Rate** | 50,000 rows/sec | **3M rows/minute** |

### **Assumption Notes**
- **MQTT Overhead**: Assumes TLS is offloaded at the Load Balancer (NLB). 1KB is the "usable" payload.
- **Compression**: Kafka `lz4` compression should reduce storage by ~2x-3x (depending on payload entropy), reducing the 90TB estimate significantly.
- **Index Overhead**: DB storage assumes ~1.5x-2x multiplier for B-tree indexes or ~1.1x for BRIN.

---

## 2. Infrastructure Recommendations

### **Kafka Layer**
- **Brokers**: **6 × r6i.4xlarge** (16 vCPU, 128GB RAM). 
    - *Rationale*: Memory is key for page cache to avoid disk reads for real-time consumers. 128GB ensures the last 30 minutes of data is always in RAM.
- **Storage**: **gp3 volumes (16,000 IOPS / 1000 MB/s throughput)**.
    - *Rationale*: Sustained 150 MB/s write + 50 MB/s read requires high throughput disks, EBS gp3 is the cost-effective sweet spot.
- **Topic Config (`iot.events.raw`)**: 
    - Partition count: **64** (Multiple of 2, allows scaling workers to 64 pods).
    - Replication: **3**.
    - Min.insync.replicas: **2**.

### **Redis Cluster (Idempotency & Rate Limiting)**
- **Cluster Size**: **12 Nodes (6 primary + 6 replicas) — m6g.4xlarge**.
    - *Rationale*: 1.1 TB memory needs to be distributed. Each primary holds ~180GB. 16 vCPU per node handles the 50K ops/sec overhead and Lua script processing easily.
- **Failover**: Multi-AZ replicas are non-negotiable for session consistency.

### **PostgreSQL Cluster (Storage & State)**
- **Scaling Strategy**: **Write Sharding (4 shards)**. 
    - *Rationale*: A single PostgreSQL primary on m6i.32xlarge maxes out around 20K–25K high-concurrency inserts/sec. 50K/sec requires 4-6 shards.
- **Primary Node**: **r6i.8xlarge** per shard.
- **Index Strategy**: Use **BRIN indexes** for the `ingested_at` time-series column. B-tree on `event_id` is required for dedup but will exceed memory eventually; plan for tablespace-sharding by month.

### **Application (K8s)**
- **Ingestion Service**: **40 Pods** (2 CPU / 4GB). Total ~80 cores.
- **Worker Service**: **128 Pods** (2 CPU / 2GB). 
    - *Rationale*: Worker pod count should be 2x partitions (using `asyncio` to saturate multiple partitions per pod or multiple pods per partition if using `cooperative-sticky`). With 64 partitions, 128 pods provides concurrency and failover headroom.

---

## 3. Burst & Auto-Scaling Strategy

| Component | Metric | Threshold | Action |
|---|---|---|---|
| **Ingestion** | CPU Usage | > 60% sustained | Scale up 5 pods recursively |
| **Worker** | Kafka Consumer Lag | > 2,000 per partition | Scale up pods to max partition count (64) |
| **Kafka Cluster** | Disk Usage | > 70% | Auto-expand EBS volumes (AWS EBS feature) |
| **Database** | WAL Write Queue | > 10% saturation | Trigger circuit breaker in Worker (pause consumption) |

### **Burst Handling: The "Circuit Breaker"**
Since DB is the eventual bottleneck:
1.  **Stage 1 (Queueing)**: Kafka acts as the primary buffer. 50K/s burst can be absorbed by Kafka for hours without dropping data.
2.  **Stage 2 (Backpressure)**: If consumer lag exceeds X minutes, the `Ingestion` service starts rejecting new MQTT messages (sending NACK to the device) to preserve system stability.
3.  **Stage 3 (Partition Re-balancing)**: In extreme bursts, temporarily double the partition count of the raw topic to allow more worker parallelism (Note: this breaks per-device ordering).

## 4. Summary Table

| Asset | Spec Recommendation | Total Qty |
|---|---|---|
| **Kafka Broker** | r6i.4xlarge (128GB RAM) | 6 |
| **Redis Node** | m6g.4xlarge (128GB RAM) | 12 |
| **Postgres Shard** | r6i.8xlarge (256GB RAM) | 4 |
| **Ingestion Pods** | 2 vCPU / 4GB RAM | 40 |
| **Worker Pods** | 2 vCPU / 2GB RAM | 128 |
| **Global LB** | AWS NLB (High Availability) | 1 |

**Infrastructure Maturity Level**: Professional / High-Scale.
**Operational Risk**: High without automated shard rebalancing for Postgres. Recommended investigation into **Citus** or **CockroachDBDistributed** for the future.
