"""Strict contracts for durable any-one/all-approver decisions.

Directory ports supply immutable actor IDs, never display names. Task creation
snapshots eligible users; membership changes require an explicit reassignment.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from nexusflow.contracts.models import Contract, Identifier, Scope

StageRule = Literal["any_one", "all_approvers"]
ApprovalRule = Literal["any_one", "all_approvers", "sequential"]
Decision = Literal["approve", "reject"]
TaskStatus = Literal["pending", "approved", "rejected"]
Timestamp = Annotated[str, Field(min_length=1, max_length=64)]
PrincipalIds = Annotated[tuple[Identifier, ...], Field(max_length=1000)]


class TaskError(Exception):
    """Stable error code with a safe, caller-visible explanation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ApprovalStage(Contract):
    stage_id: Identifier
    rule: StageRule
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()

    @model_validator(mode="after")
    def has_assignment(self) -> "ApprovalStage":
        if not self.user_ids and not self.group_ids:
            raise ValueError("At least one user or group assignment is required")
        return self


class StageAssignment(Contract):
    stage_id: Identifier
    rule: StageRule
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]


class CreateTask(Contract):
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    rule: ApprovalRule
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()

    stages: Annotated[tuple[ApprovalStage, ...], Field(max_length=20)] = ()

    @model_validator(mode="after")
    def has_assignment(self) -> "CreateTask":
        if self.rule == "sequential":
            if not self.stages or self.user_ids or self.group_ids:
                raise ValueError("Sequential tasks require stages and no root assignment")
            if len({stage.stage_id for stage in self.stages}) != len(self.stages):
                raise ValueError("Stage identifiers must be unique")
        elif self.stages:
            raise ValueError("Only sequential tasks can declare stages")
        elif not self.user_ids and not self.group_ids:
            raise ValueError("At least one user or group assignment is required")
        return self


class DecisionRequest(Contract):
    command_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    decision: Decision


class DelegationRequest(Contract):
    command_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    delegate_id: Identifier


class DelegationRecord(Contract):
    command_id: Identifier
    actor_id: Identifier
    delegate_id: Identifier
    stage_id: Identifier | None = None
    revision: Annotated[int, Field(ge=2)]
    created_at: Timestamp
    previous_eligible_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]


class ReassignmentRequest(Contract):
    command_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    user_ids: PrincipalIds = ()
    group_ids: PrincipalIds = ()

    @model_validator(mode="after")
    def has_assignment(self) -> "ReassignmentRequest":
        if not self.user_ids and not self.group_ids:
            raise ValueError("At least one user or group assignment is required")
        return self


class ReassignmentRecord(Contract):
    command_id: Identifier
    actor_id: Identifier
    revision: Annotated[int, Field(ge=2)]
    created_at: Timestamp
    user_ids: PrincipalIds
    group_ids: PrincipalIds
    previous_eligible_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]


class DecisionRecord(Contract):
    stage_id: Identifier | None = None
    command_id: Identifier
    actor_id: Identifier
    decision: Decision
    revision: Annotated[int, Field(ge=2)]
    created_at: Timestamp


class TaskView(Contract):
    stage_assignments: Annotated[tuple[StageAssignment, ...], Field(max_length=20)] = ()
    stage_index: Annotated[int, Field(ge=0, le=19)] = 0
    task_id: Identifier
    scope: Scope
    request: CreateTask
    eligible_approver_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=1000)]
    status: TaskStatus
    revision: Annotated[int, Field(ge=1)]
    decisions: tuple[DecisionRecord, ...] = ()
    assignment_history: Annotated[tuple[ReassignmentRecord, ...], Field(max_length=100)] = ()
    delegation_history: Annotated[tuple[DelegationRecord, ...], Field(max_length=100)] = ()
    created_at: Timestamp


class TaskPage(Contract):
    items: tuple[TaskView, ...]
    next_offset: Annotated[int, Field(ge=0, le=10000)] | None


class TaskEvent(Contract):
    event_id: Identifier
    task_id: Identifier
    scope: Scope
    kind: Literal[
        "created",
        "decided",
        "stage_advanced",
        "reassigned",
        "delegated",
        "form_bound",
        "form_updated",
        "completion_delivered",
        "notification_scheduled",
        "notification_delivered",
        "notification_failed",
        "notification_cancelled",
    ]
    actor_id: Identifier | None
    revision: Annotated[int, Field(ge=1)]
    created_at: Timestamp
    assignment: ReassignmentRecord | None = None
    notification_id: Identifier | None = None
    stage_id: Identifier | None = None
    previous_stage_id: Identifier | None = None
    delegation: DelegationRecord | None = None


class Completion(Contract):
    completion_id: Identifier
    task_id: Identifier
    scope: Scope
    run_id: Identifier
    node_invocation_id: Identifier
    definition_revision: Identifier
    outcome: Literal["approved", "rejected"]
    task_revision: Annotated[int, Field(ge=2)]


class CompletionBatch(Contract):
    attempted: Annotated[int, Field(ge=0, le=100)]
    delivered: Annotated[int, Field(ge=0, le=100)]
    failed: Annotated[int, Field(ge=0, le=100)]
    next_after_task_id: Identifier | None = None

    @model_validator(mode="after")
    def bounded_counts(self) -> "CompletionBatch":
        if self.delivered + self.failed > self.attempted:
            raise ValueError("Batch outcomes cannot exceed attempted completions")
        return self


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
        """Resolve trusted directory IDs within scope; failures must deny assignment.

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


class AsyncCompletionSink(Protocol):
    async def deliver(self, completion: Completion) -> None:
        """Acknowledge durable delivery, deduplicating forever by completion ID.

        Failure or cancellation leaves delivery pending. Uncertain acknowledgements
        may retry the same completion; the destination must advance at most once.
        """
        ...
