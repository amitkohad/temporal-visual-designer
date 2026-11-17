"""Per-scope admission budgets with explicit reject status."""

from threading import Lock

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.types import (
    AdmissionBudget,
    AdmissionDecision,
    DrainPhase,
    OperationsError,
)


def _key(scope: Scope) -> tuple[str, str]:
    return scope.organization_id, scope.environment_id


class FakeAdmissionPolicy:
    """Independent per-scope concurrency counters for fake-port acceptance."""

    def __init__(self) -> None:
        self._budgets: dict[tuple[str, str], AdmissionBudget] = {}
        self._in_flight: dict[tuple[str, str], set[str]] = {}
        self._phases: dict[tuple[str, str], DrainPhase] = {}
        self._lock = Lock()

    def configure(self, budget: AdmissionBudget) -> None:
        if budget.max_concurrent < 1:
            raise ValueError("Admission budget must allow at least one concurrent unit")
        with self._lock:
            key = _key(budget.scope)
            self._budgets[key] = budget
            self._in_flight.setdefault(key, set())
            self._phases.setdefault(key, "accepting")

    def set_phase(self, scope: Scope, phase: DrainPhase) -> None:
        with self._lock:
            key = _key(scope)
            if key not in self._budgets:
                raise OperationsError("not_found", "No admission budget is configured for scope")
            self._phases[key] = phase

    def phase(self, scope: Scope) -> DrainPhase:
        with self._lock:
            key = _key(scope)
            if key not in self._budgets:
                raise OperationsError("not_found", "No admission budget is configured for scope")
            return self._phases[key]

    def in_flight_ids(self, scope: Scope) -> tuple[str, ...]:
        with self._lock:
            key = _key(scope)
            return tuple(sorted(self._in_flight.get(key, set())))

    def admit(self, scope: Scope, *, work_id: str) -> AdmissionDecision:
        if type(work_id) is not str or not work_id or len(work_id) > 128:
            raise OperationsError("invalid_input", "work_id must be a non-empty identifier")
        with self._lock:
            key = _key(scope)
            budget = self._budgets.get(key)
            if budget is None:
                return AdmissionDecision(
                    admitted=False,
                    scope=scope,
                    code="rejected_unknown_scope",
                    message="No admission budget is configured for scope",
                    in_flight=0,
                    budget_remaining=0,
                )
            flight = self._in_flight.setdefault(key, set())
            phase = self._phases.get(key, "accepting")
            if phase in ("draining", "drained", "restarting"):
                return AdmissionDecision(
                    admitted=False,
                    scope=scope,
                    code="rejected_draining",
                    message="Scope is draining or restarting; new work is rejected",
                    in_flight=len(flight),
                    budget_remaining=max(0, budget.max_concurrent - len(flight)),
                )
            if work_id in flight:
                remaining = max(0, budget.max_concurrent - len(flight))
                return AdmissionDecision(
                    admitted=True,
                    scope=scope,
                    code="admitted",
                    message="Work already admitted under this scope budget",
                    in_flight=len(flight),
                    budget_remaining=remaining,
                )
            if len(flight) >= budget.max_concurrent:
                return AdmissionDecision(
                    admitted=False,
                    scope=scope,
                    code="rejected_budget",
                    message="Scope admission budget exhausted",
                    in_flight=len(flight),
                    budget_remaining=0,
                )
            flight.add(work_id)
            remaining = budget.max_concurrent - len(flight)
            return AdmissionDecision(
                admitted=True,
                scope=scope,
                code="admitted",
                message="Admitted under scope budget",
                in_flight=len(flight),
                budget_remaining=remaining,
            )

    def release(self, scope: Scope, *, work_id: str) -> None:
        with self._lock:
            key = _key(scope)
            flight = self._in_flight.get(key)
            if flight is None:
                raise OperationsError("not_found", "No admission budget is configured for scope")
            flight.discard(work_id)

    def restore_in_flight(self, scope: Scope, work_ids: tuple[str, ...]) -> None:
        """Rebuild in-flight accounting after restart without re-checking drain phase."""
        with self._lock:
            key = _key(scope)
            if key not in self._budgets:
                raise OperationsError("not_found", "No admission budget is configured for scope")
            self._in_flight[key] = set(work_ids)
