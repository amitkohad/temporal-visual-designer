"""Immutable schedules and safe at-least-once task notification contracts."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope


def utc(value: datetime) -> datetime:
    """Reject naive times and normalize all stored instants to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return value.astimezone(UTC)


class NotificationSchedule(Contract):
    schedule_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    due_at: datetime
    kind: Literal["reminder", "escalation"]

    _due_utc = field_validator("due_at")(utc)


class NotificationOccurrence(Contract):
    notification_id: Identifier
    scope: Scope
    task_id: Identifier
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    kind: Literal["reminder", "escalation"]
    due_at: datetime
    recipients: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]

    _due_utc = field_validator("due_at")(utc)


class NotificationRecord(Contract):
    request: NotificationSchedule
    occurrence: NotificationOccurrence
    status: Literal["pending", "delivered", "cancelled"]
    attempts: Annotated[int, Field(ge=0)]
    next_attempt_at: datetime
    last_error: Literal["delivery_unavailable"] | None = None

    _next_utc = field_validator("next_attempt_at")(utc)


class DeliveryBatch(Contract):
    delivered: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    cancelled: Annotated[int, Field(ge=0)]


class TaskNotificationSink(Protocol):
    def deliver(self, occurrence: NotificationOccurrence) -> None:
        """Durably deduplicate by stable notification ID before acknowledging.

        Hosts must enforce a bounded timeout. This callback runs under the task
        writer transaction and must never write back to the same database. An
        uncertain acknowledgement can cause redelivery of the identical payload.
        """
        ...
