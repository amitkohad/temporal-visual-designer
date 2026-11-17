"""Frozen F10 placement, admission, readiness and drain contracts.

Maps F10-A01–A05. Caller-supplied Temporal destinations are never trusted.
Provisioning, Azure SDK bindings and cluster migration stay out of this surface.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from nexusflow.contracts.models import Contract, Identifier, Scope

WorkerPoolId = Identifier
PlacementGeneration = Annotated[int, Field(ge=1, lt=2**63)]
BudgetLimit = Annotated[int, Field(ge=0, lt=2**31)]
Timestamp = Annotated[str, Field(min_length=1, max_length=64)]
SafeDetail = Annotated[str, Field(min_length=1, max_length=512)]

AdmissionCode = Literal[
    "admitted",
    "rejected_budget",
    "rejected_draining",
    "rejected_unknown_scope",
]
DrainPhase = Literal["accepting", "draining", "drained", "restarting"]
LifecyclePhase = DrainPhase


class OperationsError(Exception):
    """Stable error code with a safe, caller-visible explanation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PlacementTarget(Contract):
    """Trusted routing for one organization/environment scope."""

    scope: Scope
    temporal_namespace: Identifier
    task_queue: Identifier
    worker_pool: WorkerPoolId
    placement_generation: PlacementGeneration


class PlacementOverride(Contract):
    """Optional hostile caller fields; any present value must be rejected."""

    temporal_namespace: Identifier | None = None
    task_queue: Identifier | None = None
    worker_pool: WorkerPoolId | None = None


class AdmissionBudget(Contract):
    """Independent per-scope concurrency budget (starts in flight)."""

    scope: Scope
    max_concurrent: BudgetLimit


class AdmissionDecision(Contract):
    admitted: bool
    scope: Scope
    code: AdmissionCode
    message: SafeDetail
    in_flight: Annotated[int, Field(ge=0, lt=2**31)]
    budget_remaining: Annotated[int, Field(ge=0, lt=2**31)]


class ReadinessComponent(Contract):
    name: Identifier
    ready: bool
    detail: SafeDetail


class ReadinessReport(Contract):
    ready: bool
    scope: Scope
    components: Annotated[tuple[ReadinessComponent, ...], Field(max_length=32)]
    correlation_id: Identifier
    checked_at: Timestamp


class DrainStatus(Contract):
    scope: Scope
    phase: DrainPhase
    accepted_work_ids: Annotated[tuple[Identifier, ...], Field(max_length=10_000)]
    rejecting_new: bool
    message: SafeDetail


class PinnedWork(Contract):
    """Accepted durable work retains its placement; silent remapping is forbidden."""

    work_id: Identifier
    scope: Scope
    placement: PlacementTarget
    release_id: Identifier


class PlacementResolver(Protocol):
    def resolve(
        self, scope: Scope, *, override: PlacementOverride | None = None
    ) -> PlacementTarget:
        """Return configured placement; reject any caller-supplied destination override."""
        ...


class AdmissionPolicy(Protocol):
    def admit(self, scope: Scope, *, work_id: str) -> AdmissionDecision:
        """Admit or explicitly reject under the scope budget / drain phase."""
        ...

    def release(self, scope: Scope, *, work_id: str) -> None:
        """Free one admitted slot when work leaves the in-flight set."""
        ...


class ReadinessReporter(Protocol):
    def report(self, scope: Scope, *, correlation_id: str, checked_at: str) -> ReadinessReport:
        """Emit readiness without secrets or high-cardinality run labels."""
        ...


class DrainController(Protocol):
    def status(self, scope: Scope) -> DrainStatus: ...

    def begin_drain(self, scope: Scope) -> DrainStatus: ...

    def complete_restart(self, scope: Scope) -> DrainStatus:
        """Return to accepting after drain; preserve pinned accepted work."""
        ...
