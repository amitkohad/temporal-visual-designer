"""Strict contracts for durable any-one/all-approver decisions.

Directory ports supply immutable actor IDs, never display names. Task creation
snapshots eligible users; membership changes require a future explicit revision.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from nexusflow.contracts.models import Contract, Identifier, Scope

ApprovalRule = Literal["any_one", "all_approvers"]
Decision = Literal["approve", "reject"]
TaskStatus = Literal["pending", "approved", "rejected"]
Timestamp = Annotated[str, Field(min_length=1, max_length=64)]
PrincipalIds = Annotated[tuple[Identifier, ...], Field(max_length=1000)]


class TaskError(Exception):
    """Stable error code with a safe, caller-visible explanation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class CreateTask(Contract):
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    rule: ApprovalRule
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()

    @model_validator(mode="after")
    def has_assignment(self) -> "CreateTask":
        if not self.user_ids and not self.group_ids:
            raise ValueError("At least one user or group assignment is required")
        return self


class DecisionRequest(Contract):
    command_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    decision: Decision


class DecisionRecord(Contract):
    command_id: Identifier
    actor_id: Identifier
    decision: Decision
    revision: Annotated[int, Field(ge=2)]
    created_at: Timestamp


class TaskView(Contract):
    task_id: Identifier
    scope: Scope
    request: CreateTask
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    status: TaskStatus
    revision: Annotated[int, Field(ge=1)]
    decisions: tuple[DecisionRecord, ...] = ()
    created_at: Timestamp


class TaskEvent(Contract):
    event_id: Identifier
    task_id: Identifier
    scope: Scope
    kind: Literal["created", "decided", "completion_delivered"]
    actor_id: Identifier | None
    revision: Annotated[int, Field(ge=1)]
    created_at: Timestamp


class Completion(Contract):
    completion_id: Identifier
    task_id: Identifier
    scope: Scope
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    outcome: Literal["approved", "rejected"]
    task_revision: Annotated[int, Field(ge=2)]


class TaskAuthorization(Protocol):
    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str:
        """Freshly authorize and return a trusted immutable actor ID, or deny.

        Adapters must recheck policy; cached request permissions are insufficient.
        """
        ...


class AssignmentResolver(Protocol):
    def resolve(
        self, scope: Scope, user_ids: tuple[str, ...], group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Resolve trusted directory IDs within scope; failures must deny creation.

        Unknown users/groups and unavailable directory service must raise, never
        return caller-supplied IDs as a fallback. The service deduplicates users.
        """
        ...


class CompletionSink(Protocol):
    def deliver(self, completion: Completion) -> None:
        """Acknowledge durable delivery, deduplicating forever by completion ID.

        Raising leaves delivery pending. An uncertain response may be retried;
        the same completion must advance the destination workflow at most once.
        """
        ...
