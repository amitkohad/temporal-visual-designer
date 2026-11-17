"""Readiness and drain/restart status with pinned accepted work."""

from threading import Lock

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.admission import FakeAdmissionPolicy
from nexusflow.modules.operations_portability.placement import FakePlacementResolver
from nexusflow.modules.operations_portability.types import (
    DrainStatus,
    OperationsError,
    PinnedWork,
    PlacementTarget,
    ReadinessComponent,
    ReadinessReport,
)


def _key(scope: Scope) -> tuple[str, str]:
    return scope.organization_id, scope.environment_id


class FakeLifecycle:
    """Fake-port readiness/drain controller; preserves pinned placements across restart."""

    def __init__(
        self,
        placement: FakePlacementResolver,
        admission: FakeAdmissionPolicy,
    ) -> None:
        self.placement = placement
        self.admission = admission
        self._pinned: dict[tuple[str, str], dict[str, PinnedWork]] = {}
        self._lock = Lock()

    def pin_accepted(self, work: PinnedWork) -> None:
        decision = self.admission.admit(work.scope, work_id=work.work_id)
        if not decision.admitted:
            raise OperationsError(decision.code, decision.message)
        trusted = self.placement.resolve(work.scope)
        if work.placement != trusted:
            self.admission.release(work.scope, work_id=work.work_id)
            raise OperationsError(
                "incompatible_dependency",
                "Accepted work must pin the trusted placement for its scope",
            )
        with self._lock:
            bucket = self._pinned.setdefault(_key(work.scope), {})
            existing = bucket.get(work.work_id)
            if existing is not None and existing != work:
                self.admission.release(work.scope, work_id=work.work_id)
                raise OperationsError(
                    "conflict",
                    "Pinned work identity conflicts with an existing record",
                )
            bucket[work.work_id] = work

    def pinned(self, scope: Scope) -> tuple[PinnedWork, ...]:
        with self._lock:
            return tuple(
                sorted(self._pinned.get(_key(scope), {}).values(), key=lambda w: w.work_id)
            )

    def resume_pinned(self, scope: Scope, work_id: str) -> PinnedWork:
        """Resume accepted work with its pinned placement; never silently remap clusters."""
        with self._lock:
            work = self._pinned.get(_key(scope), {}).get(work_id)
        if work is None:
            raise OperationsError("not_found", "Pinned work was not found for scope")
        return work

    def diagnose_migration(self, scope: Scope, work_id: str) -> PlacementTarget:
        """Reject silent remapping when current placement diverges from the pin."""
        work = self.resume_pinned(scope, work_id)
        current = self.placement.resolve(scope)
        if (
            work.placement.temporal_namespace != current.temporal_namespace
            or work.placement.task_queue != current.task_queue
            or work.placement.worker_pool != current.worker_pool
        ):
            raise OperationsError(
                "incompatible_dependency",
                "Pinned work cannot silently migrate across placement changes",
            )
        return work.placement

    def report(self, scope: Scope, *, correlation_id: str, checked_at: str) -> ReadinessReport:
        try:
            target = self.placement.resolve(scope)
            placement_ready = True
            placement_detail = (
                f"namespace={target.temporal_namespace};queue={target.task_queue};"
                f"pool={target.worker_pool}"
            )
        except OperationsError as error:
            placement_ready = False
            placement_detail = str(error)
        try:
            phase = self.admission.phase(scope)
            admission_ready = phase == "accepting"
            admission_detail = f"phase={phase}"
        except OperationsError as error:
            admission_ready = False
            admission_detail = str(error)
        components = (
            ReadinessComponent(
                name="placement",
                ready=placement_ready,
                detail=placement_detail,
            ),
            ReadinessComponent(
                name="admission",
                ready=admission_ready,
                detail=admission_detail,
            ),
        )
        return ReadinessReport(
            ready=placement_ready and admission_ready,
            scope=scope,
            components=components,
            correlation_id=correlation_id,
            checked_at=checked_at,
        )

    def status(self, scope: Scope) -> DrainStatus:
        phase = self.admission.phase(scope)
        accepted = self.admission.in_flight_ids(scope)
        rejecting = phase != "accepting"
        messages = {
            "accepting": "Scope is accepting new work",
            "draining": "Scope is draining; new admissions are rejected",
            "drained": "Scope is drained; waiting for restart",
            "restarting": "Scope is restarting; new admissions remain rejected",
        }
        return DrainStatus(
            scope=scope,
            phase=phase,
            accepted_work_ids=accepted,
            rejecting_new=rejecting,
            message=messages[phase],
        )

    def begin_drain(self, scope: Scope) -> DrainStatus:
        self.admission.set_phase(scope, "draining")
        if not self.admission.in_flight_ids(scope):
            self.admission.set_phase(scope, "drained")
        return self.status(scope)

    def mark_drained(self, scope: Scope) -> DrainStatus:
        if self.admission.in_flight_ids(scope):
            raise OperationsError(
                "conflict",
                "Cannot mark drained while accepted work remains in flight",
            )
        self.admission.set_phase(scope, "drained")
        return self.status(scope)

    def complete_restart(self, scope: Scope) -> DrainStatus:
        self.admission.set_phase(scope, "restarting")
        pinned = self.pinned(scope)
        self.admission.restore_in_flight(scope, tuple(work.work_id for work in pinned))
        self.admission.set_phase(scope, "accepting")
        return self.status(scope)

    def finish_work(self, scope: Scope, work_id: str) -> None:
        with self._lock:
            bucket = self._pinned.get(_key(scope), {})
            bucket.pop(work_id, None)
        self.admission.release(scope, work_id=work_id)
        if self.admission.phase(scope) == "draining" and not self.admission.in_flight_ids(scope):
            self.admission.set_phase(scope, "drained")
