"""NF-T017a: cross-organization/environment denial smoke (fake-port)."""

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.service import OperationsPortabilityService
from nexusflow.modules.operations_portability.types import OperationsError, PlacementTarget


def _register(service: OperationsPortabilityService, org: str, env: str) -> PlacementTarget:
    scope = Scope(organization_id=org, environment_id=env)
    target = PlacementTarget(
        scope=scope,
        temporal_namespace=f"{org}-{env}",
        task_queue=f"{org}-{env}-q",
        worker_pool=f"pool-{org}",
        placement_generation=1,
    )
    service.register_scope(target, max_concurrent=2)
    return target


def test_cross_organization_placement_and_admission_are_isolated():
    service = OperationsPortabilityService()
    acme = _register(service, "acme", "dev")
    globex = _register(service, "globex", "dev")

    service.admit(acme.scope, work_id="acme-run", release_id="r1")
    assert service.resume(acme.scope, "acme-run").scope == acme.scope

    with pytest.raises(OperationsError) as missing:
        service.resume(globex.scope, "acme-run")
    assert missing.value.code == "not_found"

    foreign = Scope(organization_id="acme", environment_id="prod")
    with pytest.raises(OperationsError) as unknown:
        service.resolve_placement(foreign)
    assert unknown.value.code == "not_found"

    decision = service.try_admit(foreign, work_id="prod-run")
    assert decision.admitted is False
    assert decision.code == "rejected_unknown_scope"


def test_environment_isolation_within_organization():
    service = OperationsPortabilityService()
    dev = _register(service, "acme", "dev")
    uat = _register(service, "acme", "uat")

    service.admit(dev.scope, work_id="dev-only", release_id="r-dev")
    assert service.try_admit(uat.scope, work_id="uat-1").admitted is True
    with pytest.raises(OperationsError) as error:
        service.resume(uat.scope, "dev-only")
    assert error.value.code == "not_found"
    assert (
        service.resolve_placement(dev.scope).task_queue
        != service.resolve_placement(uat.scope).task_queue
    )
