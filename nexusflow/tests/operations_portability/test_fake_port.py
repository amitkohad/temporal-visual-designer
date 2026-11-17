"""Fake-port acceptance for F10-A01/A02/A03/A05 (no Azure)."""

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.service import OperationsPortabilityService
from nexusflow.modules.operations_portability.types import (
    OperationsError,
    PlacementOverride,
    PlacementTarget,
)


def _target(
    org: str, env: str, *, generation: int = 1, queue: str | None = None
) -> PlacementTarget:
    scope = Scope(organization_id=org, environment_id=env)
    suffix = f"{org}-{env}"
    return PlacementTarget(
        scope=scope,
        temporal_namespace=suffix,
        task_queue=queue or f"{suffix}-q",
        worker_pool=f"pool-{org}",
        placement_generation=generation,
    )


def test_rejects_caller_supplied_placement_overrides():
    service = OperationsPortabilityService()
    target = _target("acme", "dev")
    service.register_scope(target, max_concurrent=2)
    with pytest.raises(OperationsError) as error:
        service.resolve_placement(
            target.scope, override=PlacementOverride(temporal_namespace="evil")
        )
    assert error.value.code == "forbidden"
    assert service.resolve_placement(target.scope) == target


def test_independent_per_scope_admission_budgets():
    service = OperationsPortabilityService()
    acme = _target("acme", "dev")
    other = _target("globex", "dev")
    service.register_scope(acme, max_concurrent=1)
    service.register_scope(other, max_concurrent=1)

    first = service.admit(acme.scope, work_id="a1", release_id="r1")
    assert first.work_id == "a1"
    rejected = service.try_admit(acme.scope, work_id="a2")
    assert rejected.admitted is False
    assert rejected.code == "rejected_budget"

    second = service.admit(other.scope, work_id="g1", release_id="r1")
    assert second.scope == other.scope
    assert service.try_admit(other.scope, work_id="g2").code == "rejected_budget"


def test_drain_restart_preserves_pinned_work():
    service = OperationsPortabilityService()
    target = _target("acme", "dev")
    service.register_scope(target, max_concurrent=2)
    pinned = service.admit(target.scope, work_id="run-1", release_id="rel-9")

    draining = service.begin_drain(target.scope)
    assert draining.phase == "draining"
    assert draining.rejecting_new is True
    assert "run-1" in draining.accepted_work_ids
    assert service.try_admit(target.scope, work_id="run-2").code == "rejected_draining"

    restarted = service.complete_restart(target.scope)
    assert restarted.phase == "accepting"
    resumed = service.resume(target.scope, "run-1")
    assert resumed == pinned
    assert resumed.placement == target


def test_placement_change_does_not_silently_migrate_pinned_runs():
    service = OperationsPortabilityService()
    original = _target("acme", "dev", generation=1)
    service.register_scope(original, max_concurrent=2)
    service.admit(original.scope, work_id="run-1", release_id="rel-1")

    replacement = _target("acme", "dev", generation=2, queue="acme-dev-q-b")
    service.replace_placement_for_new_work(replacement)

    resumed = service.resume(original.scope, "run-1")
    assert resumed.placement == original
    with pytest.raises(OperationsError) as error:
        service.diagnose_migration(original.scope, "run-1")
    assert error.value.code == "incompatible_dependency"

    fresh = service.admit(original.scope, work_id="run-2", release_id="rel-2")
    assert fresh.placement == replacement


def test_readiness_reports_without_secrets():
    service = OperationsPortabilityService()
    target = _target("acme", "uat")
    service.register_scope(target, max_concurrent=3)
    report = service.readiness(
        target.scope, correlation_id="corr-1", checked_at="2026-11-17T00:00:00Z"
    )
    assert report.ready is True
    assert report.correlation_id == "corr-1"
    names = {component.name for component in report.components}
    assert names == {"placement", "admission"}
    assert all("secret" not in component.detail.lower() for component in report.components)

    unknown = Scope(organization_id="missing", environment_id="dev")
    unreadiness = service.readiness(
        unknown, correlation_id="corr-2", checked_at="2026-11-17T00:00:01Z"
    )
    assert unreadiness.ready is False
