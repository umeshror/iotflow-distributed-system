### [STRATEGIC] 1. Implement Local Disk Spillover (RocksDB)
**Context**: During extended Kafka outages, our 10,000-event memory buffer in the Ingestion Service can fill up.
**Task**: Use a local persistent store to buffer messages on disk when Kafka is unavailable.
**Staff Signal**: Showcases "Worst-Day" resilience thinking.

### [STRATEGIC] 2. Distributed Tracing (OpenTelemetry)
**Context**: We need per-event visibility across the Ingestion -> Kafka -> Worker lifecycle.
**Task**: Instrument the `Pipeline` utility with OTEL spans to track end-to-end latency.
**Staff Signal**: Highlights observability maturity.

### [STRATEGIC] 3. Handle Kafka Consumer Lag (Dynamic Scaling)
**Context**: Sudden traffic bursts can cause Kafka lag.
**Task**: Enhance the KEDA-driven scaling to adjust worker count dynamically based on lag thresholds.
**Staff Signal**: Demonstrates deep understanding of event-driven bottlenecks.

### [STRATEGIC] 4. Advanced Observability Dashboard
**Context**: Current Grafana dashboard is static.
**Task**: Add per-device and per-handler metrics to pinpoint latency spikes in the pipeline.
**Staff Signal**: Shows operational focus.
