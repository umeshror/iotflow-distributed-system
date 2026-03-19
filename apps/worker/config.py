"""Worker Service Configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    SERVICE_NAME: str = "iotflow-worker"
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"
    WORKER_ID: str = "worker-1"  # unique per pod — set via Kubernetes downward API

    # -------------------------------------------------------------------------
    # Kafka (Consumer)
    # -------------------------------------------------------------------------
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"
    KAFKA_TOPIC_RAW: str = "iot.events.raw"
    KAFKA_TOPIC_DLQ: str = "iot.events.dlq"
    KAFKA_CONSUMER_GROUP: str = "iotflow-workers"
    KAFKA_AUTO_OFFSET_RESET: str = "earliest"
    KAFKA_MAX_POLL_RECORDS: int = 100
    KAFKA_SESSION_TIMEOUT_MS: int = 30_000
    KAFKA_HEARTBEAT_INTERVAL_MS: int = 10_000

    # -------------------------------------------------------------------------
    # Retry policy
    # -------------------------------------------------------------------------
    RETRY_MAX_ATTEMPTS: int = 5
    RETRY_BASE_DELAY_SECONDS: float = 1.0
    RETRY_MULTIPLIER: float = 2.0
    RETRY_MAX_DELAY_SECONDS: float = 30.0
    RETRY_JITTER_FACTOR: float = 0.25  # ±25%

    # -------------------------------------------------------------------------
    # Backpressure — max concurrent DB writes per worker pod
    # -------------------------------------------------------------------------
    MAX_CONCURRENT_TASKS: int = 50

    # -------------------------------------------------------------------------
    # Redis (idempotency)
    # -------------------------------------------------------------------------
    REDIS_URL: str = "redis://redis:6379/0"
    IDEMPOTENCY_TTL_SECONDS: int = 259_200  # 72 hours

    # -------------------------------------------------------------------------
    # PostgreSQL
    # -------------------------------------------------------------------------
    DATABASE_URL: str = "postgresql://iotflow:iotflow@postgres:5432/iotflow"
    DB_POOL_MIN_SIZE: int = 5
    DB_POOL_MAX_SIZE: int = 20
    DB_COMMAND_TIMEOUT: float = 10.0

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    METRICS_PORT: int = 9101


settings = Settings()
