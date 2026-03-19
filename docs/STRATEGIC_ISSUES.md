### [STRATEGIC] 1. Implement Local Disk Spillover (RocksDB)
**Context**: During extended Kafka outages, our 10,000-event memory buffer in the Ingestion Service can fill up, leading to data loss for incoming MQTT events.
**Task**: Integrate a local persistent store (e.g., RocksDB or a simple WAL) to buffer messages on disk when Kafka is unavailable.
**Staff Signal**: Shows deep thinking about "The Worst Day" scenarios.

### [STRATEGIC] 2. Distributed Tracing (OpenTelemetry)
**Context**: While we have aggregate metrics, we lacks per-event visibility across the Ingestion -> Kafka -> Worker lifecycle.
**Task**: Instrument the `Pipeline` utility with OTEL spans to track end-to-end latency and trace "lost" events.
**Staff Signal**: Highlights observability maturity beyond simple logs.

### [STRATEGIC] 3. Dynamic Rate-Limit Provisioning
**Context**: Current rate limits are static. We need a way to auto-adjust limits based on the device's "health" or subscription tier in real-time.
**Task**: Refactor `RateLimitHandler` to fetch dynamic thresholds from Redis or a Cache-aside DB.
**Staff Signal**: Demonstrates thinking about multi-tenancy and cost-optimization.
