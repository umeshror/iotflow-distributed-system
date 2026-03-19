"""
Shared Pydantic models for IoTFlow.
Used by both the ingestion service and worker service.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class IoTEventPayload(BaseModel):
    """Arbitrary sensor payload — validated to be a non-empty dict."""

    model_config = {"extra": "allow"}


class IoTEventMetadata(BaseModel):
    """Optional device metadata attached to every event."""

    firmware: str | None = None
    signal_strength_dbm: int | None = None

    model_config = {"extra": "allow"}


class IoTEvent(BaseModel):
    """
    Canonical IoT event model shared across all services.

    Field rules:
    - event_id : ULID (26 uppercase alphanumeric chars)
    - device_id: max 128 chars, url-safe characters only
    - timestamp : ISO 8601 UTC — converted to aware datetime
    """

    event_id: str = Field(..., min_length=26, max_length=26, description="ULID event identifier")
    device_id: str = Field(..., min_length=1, max_length=128)
    event_type: str = Field(..., min_length=1, max_length=64)
    schema_version: str = Field(default="1.0")
    timestamp: datetime
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def validate_ulid(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", v, re.IGNORECASE):
            raise ValueError("event_id must be a valid ULID (26 chars, Crockford Base32)")
        return v.upper()

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        elif isinstance(v, datetime):
            dt = v
        else:
            raise ValueError("timestamp must be an ISO 8601 string or datetime")
        if dt.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return dt.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_payload_not_empty(self) -> "IoTEvent":
        if not self.payload:
            raise ValueError("payload must not be empty")
        return self

    def to_kafka_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for Kafka message value."""
        return self.model_dump(mode="json")


class DLQEvent(BaseModel):
    """Envelope wrapping a failed IoT event for the Dead Letter Queue."""

    dlq_id: str
    original_event: dict[str, Any]
    original_topic: str
    original_partition: int | None = None
    original_offset: int | None = None
    error_type: str
    error_message: str
    retry_count: int
    failed_at: datetime
    worker_id: str | None = None

    def to_kafka_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
