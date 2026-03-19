# IoTFlow Roadmap & Open Issues

This document lists planned improvements and areas where we welcome community contributions. If you'd like to work on any of these, please open an issue on GitHub to discuss the implementation details.

## 🚀 High Priority (Starter Issues)

### 1. Improve Unit Test Coverage
**Context**: While the core logic is robust, we need more comprehensive unit tests for the `EventProcessor` and `DLQPublisher`.
**Goal**: Increase coverage to >80% for all services.
**Labels**: `good first issue`, `testing`

### 2. CLI for DLQ Replay
**Context**: Currently, DLQ events are stored in PostgreSQL and Kafka, but replaying them requires manual database intervention.
**Goal**: Create a Python CLI tool (using `click` or `typer`) that can fetch events from the `dlq_events` table and re-inject them into the original Kafka topic.
**Labels**: `enhancement`, `tooling`

### 3. OpenTelemetry (OTEL) Integration
**Context**: We have Prometheus metrics, but we lack distributed tracing across the ingestion and worker services.
**Goal**: Add OTEL instrumentation to track an event's lifecycle from MQTT reception to final DB commit.
**Labels**: `observability`, `advanced`

## 🛠️ Infrastructure Improvements

### 4. Local Disk Spillover for Ingestion
**Context**: If Kafka is down, the ingestion service currently drops messages once its 10,000-event memory buffer is full.
**Goal**: Use a local persistent storage (e.g., RocksDB or a simple file-backed queue) to buffer messages on disk during extended Kafka outages.
**Labels**: `resilience`, `advanced`

### 5. Schema Registry Support
**Context**: Schema validation is currently done via static Pydantic models.
**Goal**: Integrate with Confluent Schema Registry to support Avro/Protobuf and ensure backward/forward compatibility across device updates.
**Labels**: `data-integrity`

## 📊 Dashboard & UI

### 6. Dynamic Grafana Dashboards
**Context**: The current dashboard is static.
**Goal**: Update the dashboard to use Grafana variables to filter by `device_id` or `event_type`.
**Labels**: `observability`, `ui`

---

*Found something else? [Open an issue](https://github.com/USER/Iot-flow/issues/new)!*
