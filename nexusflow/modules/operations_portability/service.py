"""Compose fake-port placement, admission and lifecycle for F10 acceptance."""

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.admission import FakeAdmissionPolicy
from nexusflow.modules.operations_portability.lifecycle import FakeLifecycle
from nexusflow.modules.operations_portability.placement import FakePlacementResolver
from nexusflow.modules.operations_portability.types import (
    AdmissionBudget,
    AdmissionDecision,
    DrainStatus,
    OperationsError,
    PinnedWork,
    PlacementOverride,
    PlacementTarget,
    ReadinessReport,
)


class OperationsPortabilityService:
    """Minimal fake-port operations surface; no Azure or provisioning APIs."""

    def __init__(
        self,
        placement: FakePlacementResolver | None = None,
        admission: FakeAdmissionPolicy | None = None,
    ) -> None:
        self.placement = placement or FakePlacementResolver()
        self.admission = admission or FakeAdmissionPolicy()
        self.lifecycle = FakeLifecycle(self.placement, self.admission)

    def register_scope(
        self,
        target: PlacementTarget,
        *,
        max_concurrent: int,
    ) -> None:
        self.placement.register(target)
        self.admission.configure(AdmissionBudget(scope=target.scope, max_concurrent=max_concurrent))

    def resolve_placement(
        self, scope: Scope, *, override: PlacementOverride | None = None
    ) -> PlacementTarget:
        return self.placement.resolve(scope, override=override)

    def admit(self, scope: Scope, *, work_id: str, release_id: str) -> PinnedWork:
        target = self.placement.resolve(scope)
        work = PinnedWork(
            work_id=work_id,
            scope=scope,
            placement=target,
            release_id=release_id,
        )
        self.lifecycle.pin_accepted(work)
        return work

    def try_admit(self, scope: Scope, *, work_id: str) -> AdmissionDecision:
        return self.admission.admit(scope, work_id=work_id)

    def readiness(self, scope: Scope, *, correlation_id: str, checked_at: str) -> ReadinessReport:
        return self.lifecycle.report(scope, correlation_id=correlation_id, checked_at=checked_at)

    def drain_status(self, scope: Scope) -> DrainStatus:
        return self.lifecycle.status(scope)

    def begin_drain(self, scope: Scope) -> DrainStatus:
        return self.lifecycle.begin_drain(scope)

    def complete_restart(self, scope: Scope) -> DrainStatus:
        return self.lifecycle.complete_restart(scope)

    def resume(self, scope: Scope, work_id: str) -> PinnedWork:
        return self.lifecycle.resume_pinned(scope, work_id)

    def diagnose_migration(self, scope: Scope, work_id: str) -> PlacementTarget:
        return self.lifecycle.diagnose_migration(scope, work_id)

    def finish(self, scope: Scope, work_id: str) -> None:
        self.lifecycle.finish_work(scope, work_id)

    def replace_placement_for_new_work(self, target: PlacementTarget) -> None:
        current = self.placement.resolve(target.scope)
        if (
            current.temporal_namespace == target.temporal_namespace
            and current.task_queue == target.task_queue
            and current.worker_pool == target.worker_pool
            and current.placement_generation >= target.placement_generation
        ):
            raise OperationsError(
                "conflict",
                "Replacement placement must advance generation or change routing",
            )
        self.placement.replace_for_new_work(target)
