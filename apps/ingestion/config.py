"""
Ingestion Service Configuration — loaded from environment variables via Pydantic Settings.
All values have safe defaults for local Docker Compose development.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # -------------------------------------------------------------------------
    # Service identity
    # -------------------------------------------------------------------------
    SERVICE_NAME: str = "iotflow-ingestion"
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"  # development | staging | production

    # -------------------------------------------------------------------------
    # MQTT
    # -------------------------------------------------------------------------
    MQTT_HOST: str = "mosquitto"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = ""
    MQTT_PASSWORD: str = ""
    MQTT_TLS_ENABLED: bool = False
    MQTT_TOPIC_PREFIX: str = "devices/+/events"
    MQTT_KEEPALIVE_SECONDS: int = 60
    MQTT_RECONNECT_DELAY_MIN: float = 1.0
    MQTT_RECONNECT_DELAY_MAX: float = 30.0

    # -------------------------------------------------------------------------
    # Kafka
    # -------------------------------------------------------------------------
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:9092"
    KAFKA_TOPIC_RAW: str = "iot.events.raw"
    KAFKA_ACKS: str = "all"          # strongest durability
    KAFKA_LINGER_MS: int = 5         # micro-batch for throughput
    KAFKA_COMPRESSION_TYPE: str = "none"
    KAFKA_MAX_BLOCK_MS: int = 5000   # backpressure — block instead of drop

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    REDIS_URL: str = "redis://redis:6379/0"
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RATE_LIMIT_MAX_EVENTS: int = 100  # per device per window

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    METRICS_PORT: int = 9100
    OTEL_ENABLED: bool = False


settings = Settings()
