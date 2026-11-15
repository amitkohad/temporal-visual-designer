"""Frozen schedule-start contracts (ADR 0069); adapters own persistence and fire."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope


class ScheduleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


OverlapPolicy = Literal["allow", "skip"]


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


def validate_iana_timezone(value: str) -> str:
    if not value or len(value) > 64 or value.startswith("/") or ".." in value:
        raise ScheduleError("invalid_timezone", "Timezone must be a valid IANA name")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, IsADirectoryError, ValueError, OSError):
        raise ScheduleError("invalid_timezone", "Timezone must be a valid IANA name") from None
    return value


def occurrence_idempotency_key(schedule_id: str, occurrence_utc: datetime) -> str:
    """Stable Identifier for one civil occurrence converted to UTC whole seconds."""
    instant = require_aware_utc(occurrence_utc).replace(microsecond=0)
    key = f"{schedule_id}.{instant.strftime('%Y%m%dT%H%M%SZ')}"
    if len(key) > 128:
        raise ScheduleError("invalid_input", "Occurrence idempotency key exceeds Identifier limit")
    return key


class ScheduleDefinition(Contract):
    schedule_id: Identifier
    scope: Scope
    project_id: Identifier
    workflow_id: Identifier
    cron: Annotated[str, Field(min_length=5, max_length=128)]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    overlap_policy: OverlapPolicy
    input_json: Annotated[str, Field(max_length=65536)] = "{}"
    enabled: bool = True


class ScheduleRecord(Contract):
    definition: ScheduleDefinition
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


class ScheduleOccurrenceRecord(Contract):
    schedule_id: Identifier
    occurrence_at: datetime
    idempotency_key: Identifier
    status: Literal["started", "skipped", "pending"]
    execution_id: Identifier | None = None

    @field_validator("occurrence_at")
    @classmethod
    def occurrence_utc(cls, value: datetime) -> datetime:
        return require_aware_utc(value)


class ScheduleClock(Protocol):
    def now(self) -> datetime:
        """Aware UTC instant used for due-occurrence evaluation."""
        ...


class ScheduleAuthorization(Protocol):
    def authorize(self, scope: Scope, project_id: str) -> None:
        """Freshly authorize schedule mutate/read; identity adapters own policy."""
        ...
