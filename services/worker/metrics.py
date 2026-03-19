"""
Prometheus metrics for the Worker Service.
Exposed at :9101/metrics.
"""

from prometheus_client import Counter, Histogram, Gauge, start_http_server

from config import settings

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
EVENTS_CONSUMED = Counter(
    "iotflow_worker_events_consumed_total",
    "Total events consumed from Kafka",
)

EVENTS_PROCESSED = Counter(
    "iotflow_worker_events_processed_total",
    "Events successfully written to PostgreSQL",
    ["event_type"],
)

EVENTS_DUPLICATE = Counter(
    "iotflow_worker_events_duplicate_total",
    "Events detected as duplicates (idempotency hit)",
)

EVENTS_DLQ = Counter(
    "iotflow_worker_events_dlq_total",
    "Events sent to Dead Letter Queue after exhausting retries",
    ["error_type"],
)

RETRY_ATTEMPTS = Counter(
    "iotflow_worker_retry_attempts_total",
    "Total retry attempts across all events",
    ["attempt_number"],
)

DB_ERRORS = Counter(
    "iotflow_worker_db_errors_total",
    "Database write errors",
    ["error_type"],
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------
PROCESSING_LATENCY = Histogram(
    "iotflow_worker_processing_duration_seconds",
    "End-to-end processing time per event (consume → DB write)",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

DB_WRITE_LATENCY = Histogram(
    "iotflow_worker_db_write_duration_seconds",
    "Time spent on PostgreSQL INSERT",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

IDEMPOTENCY_CHECK_LATENCY = Histogram(
    "iotflow_worker_idempotency_check_duration_seconds",
    "Time spent on Redis idempotency check",
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.05],
)

# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------
CONSUMER_LAG = Gauge(
    "iotflow_worker_consumer_lag",
    "Approximate Kafka consumer lag (messages behind)",
    ["partition"],
)

ACTIVE_TASKS = Gauge(
    "iotflow_worker_active_tasks",
    "Number of in-flight event processing tasks",
)


def start_metrics_server() -> None:
    start_http_server(settings.METRICS_PORT)
