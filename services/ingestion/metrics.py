"""
Prometheus metrics for the Ingestion Service.
Exposed at :9100/metrics via a background thread server.
"""

from prometheus_client import Counter, Histogram, Gauge, start_http_server

from config import settings

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
EVENTS_RECEIVED = Counter(
    "iotflow_ingestion_events_received_total",
    "Total MQTT messages received",
    ["device_id", "event_type"],
)

EVENTS_VALIDATED = Counter(
    "iotflow_ingestion_events_validated_total",
    "Events that passed schema validation",
)

EVENTS_DROPPED = Counter(
    "iotflow_ingestion_events_dropped_total",
    "Events dropped due to validation failure or rate limiting",
    ["reason"],  # labels: schema_error | rate_limited | missing_field
)

KAFKA_PRODUCE_ERRORS = Counter(
    "iotflow_ingestion_kafka_produce_errors_total",
    "Failed Kafka produce attempts",
    ["error_type"],
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------
VALIDATION_LATENCY = Histogram(
    "iotflow_ingestion_validation_duration_seconds",
    "Time spent validating an MQTT message",
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
)

KAFKA_PRODUCE_LATENCY = Histogram(
    "iotflow_ingestion_kafka_produce_duration_seconds",
    "Time for Kafka produce to complete",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------
MQTT_CONNECTED = Gauge(
    "iotflow_ingestion_mqtt_connected",
    "1 if connected to MQTT broker, 0 otherwise",
)

QUEUE_SIZE = Gauge(
    "iotflow_ingestion_internal_queue_size",
    "Number of messages waiting in internal asyncio queue",
)


def start_metrics_server() -> None:
    """Start Prometheus HTTP metrics server on configured port."""
    start_http_server(settings.METRICS_PORT)
