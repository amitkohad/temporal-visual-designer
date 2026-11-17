"""NF-T017a: drain/restart and pinned recovery smoke (fake-port)."""

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.service import OperationsPortabilityService
from nexusflow.modules.operations_portability.types import OperationsError, PlacementTarget


def test_drain_restart_preserves_accepted_work_and_rejects_new():
    service = OperationsPortabilityService()
    scope = Scope(organization_id="acme", environment_id="dev")
    service.register_scope(
        PlacementTarget(
            scope=scope,
            temporal_namespace="acme-dev",
            task_queue="acme-dev-q",
            worker_pool="pool-acme",
            placement_generation=1,
        ),
        max_concurrent=3,
    )
    first = service.admit(scope, work_id="accepted-1", release_id="rel-1")
    second = service.admit(scope, work_id="accepted-2", release_id="rel-1")

    draining = service.begin_drain(scope)
    assert draining.rejecting_new is True
    assert set(draining.accepted_work_ids) == {"accepted-1", "accepted-2"}
    assert service.try_admit(scope, work_id="late").code == "rejected_draining"

    service.finish(scope, "accepted-2")
    status = service.drain_status(scope)
    assert "accepted-2" not in status.accepted_work_ids
    assert status.phase == "draining"

    service.finish(scope, "accepted-1")
    assert service.drain_status(scope).phase == "drained"

    # Re-admit before drain for restart preservation path.
    service.complete_restart(scope)
    revived = service.admit(scope, work_id="accepted-3", release_id="rel-2")
    service.begin_drain(scope)
    restarted = service.complete_restart(scope)
    assert restarted.phase == "accepting"
    assert service.resume(scope, "accepted-3") == revived
    assert first.work_id == "accepted-1"
    assert second.work_id == "accepted-2"


def test_pinned_recovery_after_placement_replacement():
    service = OperationsPortabilityService()
    scope = Scope(organization_id="acme", environment_id="dev")
    original = PlacementTarget(
        scope=scope,
        temporal_namespace="acme-dev",
        task_queue="acme-dev-q",
        worker_pool="pool-acme",
        placement_generation=1,
    )
    service.register_scope(original, max_concurrent=2)
    pinned = service.admit(scope, work_id="legacy", release_id="rel-legacy")

    service.replace_placement_for_new_work(
        PlacementTarget(
            scope=scope,
            temporal_namespace="acme-dev",
            task_queue="acme-dev-q-v2",
            worker_pool="pool-acme",
            placement_generation=2,
        )
    )
    resumed = service.resume(scope, "legacy")
    assert resumed.placement == original
    assert resumed == pinned
    with pytest.raises(OperationsError) as error:
        service.diagnose_migration(scope, "legacy")
    assert error.value.code == "incompatible_dependency"
