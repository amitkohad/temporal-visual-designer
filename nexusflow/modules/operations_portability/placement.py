"""Trusted scope→placement resolution; rejects caller destination overrides."""

from threading import Lock

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.types import (
    OperationsError,
    PlacementOverride,
    PlacementTarget,
)


def _key(scope: Scope) -> tuple[str, str]:
    return scope.organization_id, scope.environment_id


class FakePlacementResolver:
    """In-memory placement table for fake-port acceptance (not cloud provisioning)."""

    def __init__(self) -> None:
        self._targets: dict[tuple[str, str], PlacementTarget] = {}
        self._lock = Lock()

    def register(self, target: PlacementTarget) -> None:
        with self._lock:
            self._targets[_key(target.scope)] = target

    def resolve(
        self, scope: Scope, *, override: PlacementOverride | None = None
    ) -> PlacementTarget:
        if override is not None and (
            override.temporal_namespace is not None
            or override.task_queue is not None
            or override.worker_pool is not None
        ):
            raise OperationsError(
                "forbidden",
                "Caller-supplied placement overrides are rejected",
            )
        with self._lock:
            target = self._targets.get(_key(scope))
        if target is None:
            raise OperationsError("not_found", "No placement is configured for scope")
        return target

    def replace_for_new_work(self, target: PlacementTarget) -> None:
        """Update placement for future admissions only; pinned work is unchanged."""
        self.register(target)
