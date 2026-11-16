"""Frozen form-start contracts (ADR 0071); adapters own persistence and submit."""

from datetime import UTC, datetime
from typing import Annotated, Protocol

from pydantic import Field, field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope


class FormStartError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


class FormStartDefinition(Contract):
    form_start_id: Identifier
    scope: Scope
    project_id: Identifier
    workflow_id: Identifier
    form_definition_json: Annotated[str, Field(min_length=2, max_length=1_048_576)]
    enabled: bool = True


class FormStartRecord(Contract):
    definition: FormStartDefinition
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


class FormStartSubmissionRecord(Contract):
    form_start_id: Identifier
    idempotency_key: Identifier
    submitted_at: datetime
    execution_id: Identifier | None = None

    @field_validator("submitted_at")
    @classmethod
    def submitted_utc(cls, value: datetime) -> datetime:
        return require_aware_utc(value)


class FormStartClock(Protocol):
    def now(self) -> datetime:
        """Aware UTC instant used for create/disable timestamps."""
        ...


class FormStartAuthorization(Protocol):
    def authorize(self, scope: Scope, project_id: str) -> None:
        """Freshly authorize form-start mutate/submit; identity adapters own policy."""
        ...
