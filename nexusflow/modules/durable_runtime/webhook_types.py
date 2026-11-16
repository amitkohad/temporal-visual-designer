"""Frozen webhook-start contracts (ADR 0070); adapters own persistence and deliver."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope


class WebhookError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


def digest_webhook_secret(secret: str) -> str:
    """SHA-256 hex digest of the shared secret; never persist the raw secret."""
    import hashlib

    if not secret or len(secret.encode("utf-8")) > 256:
        raise WebhookError("invalid_input", "Webhook secret must be 1–256 UTF-8 bytes")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_delivery_timestamp(raw: str) -> datetime:
    """Unix epoch seconds (digits only) as aware UTC."""
    if not raw or len(raw) > 16 or not raw.isdigit():
        raise WebhookError("invalid_input", "Webhook timestamp must be Unix epoch seconds")
    try:
        instant = datetime.fromtimestamp(int(raw), tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise WebhookError(
            "invalid_input", "Webhook timestamp must be Unix epoch seconds"
        ) from None
    return instant


class WebhookDefinition(Contract):
    webhook_id: Identifier
    scope: Scope
    project_id: Identifier
    workflow_id: Identifier
    secret_digest: Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]
    replay_window_seconds: Annotated[int, Field(ge=1, le=3600)] = 300
    enabled: bool = True


class WebhookRecord(Contract):
    definition: WebhookDefinition
    created_at: datetime
    disabled_at: datetime | None = None

    @field_validator("created_at")
    @classmethod
    def created_utc(cls, value: datetime) -> datetime:
        return require_aware_utc(value)

    @field_validator("disabled_at")
    @classmethod
    def disabled_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware_utc(value)


class WebhookDeliveryRecord(Contract):
    webhook_id: Identifier
    delivery_id: Identifier
    received_at: datetime
    status: Literal["started", "rejected"]
    execution_id: Identifier | None = None

    @field_validator("received_at")
    @classmethod
    def received_utc(cls, value: datetime) -> datetime:
        return require_aware_utc(value)


class WebhookClock(Protocol):
    def now(self) -> datetime:
        """Aware UTC instant used for replay-window evaluation."""
        ...


class WebhookAuthorization(Protocol):
    def authorize(self, scope: Scope, project_id: str) -> None:
        """Freshly authorize webhook admin mutate/read; identity adapters own policy."""
        ...
