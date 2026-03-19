# 🐞 Strategic Issues & Future Roadmap

These are "Smart Hack" issues that showcase the long-term thinking and operational maturity of the project.

### 1. [Good First Issue] Add Exponential Retry Mechanism
**Status**: Planned  
**Context**: We need a configurable backoff strategy for transient DB/Kafka failures.
**Task**: Refactor `PersistenceHandler` to use dynamic backoff parameters.

### 2. [Enhancement] Implement Idempotency using Redis
**Status**: Implemented (Enhancement: Multi-AZ support)  
**Context**: Current idempotency check is single-instance.
**Task**: Transition to `Redlock` or Redis Cluster for cross-region deduplication.

### 3. [Performance] Handle Kafka Consumer Lag
**Status**: Critical  
**Context**: Sudden traffic bursts can outpace a single consumer group.
**Task**: Integrate KEDA (Kubernetes Event-driven Autoscaling) to scale worker pods based on lag metrics.

### 4. [Observability] Add Metrics Dashboard
**Status**: In Progress  
**Context**: We need real-time p99 latency visibility for each pipeline handler.
**Task**: Export handler-level Prometheus metrics for the Grafana dashboard.
