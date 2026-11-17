"""Contract shape and protocol surface for F10-T01a."""

import pytest
from pydantic import ValidationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.operations_portability.types import (
    AdmissionBudget,
    AdmissionDecision,
    DrainStatus,
    PlacementOverride,
    PlacementTarget,
    ReadinessComponent,
    ReadinessReport,
)


def test_placement_target_is_frozen_and_scoped():
    scope = Scope(organization_id="acme", environment_id="dev")
    target = PlacementTarget(
        scope=scope,
        temporal_namespace="acme-dev",
        task_queue="acme-dev-q",
        worker_pool="shared-a",
        placement_generation=1,
    )
    with pytest.raises(ValidationError):
        target.temporal_namespace = "other"  # type: ignore[misc]
    assert target.scope == scope


def test_admission_and_readiness_reject_extra_fields():
    scope = Scope(organization_id="acme", environment_id="dev")
    with pytest.raises(ValidationError):
        AdmissionBudget.model_validate(
            {"scope": scope.model_dump(), "max_concurrent": 1, "azure_sku": "P1"}
        )
    with pytest.raises(ValidationError):
        ReadinessReport.model_validate(
            {
                "ready": True,
                "scope": scope.model_dump(),
                "components": [],
                "correlation_id": "c1",
                "checked_at": "t0",
                "secret": "x",
            }
        )


def test_decision_and_drain_status_codes():
    scope = Scope(organization_id="acme", environment_id="dev")
    decision = AdmissionDecision(
        admitted=False,
        scope=scope,
        code="rejected_budget",
        message="Scope admission budget exhausted",
        in_flight=2,
        budget_remaining=0,
    )
    assert decision.admitted is False
    status = DrainStatus(
        scope=scope,
        phase="draining",
        accepted_work_ids=("w1",),
        rejecting_new=True,
        message="Scope is draining; new admissions are rejected",
    )
    assert status.rejecting_new is True
    component = ReadinessComponent(name="placement", ready=True, detail="ok")
    assert component.ready is True
    override = PlacementOverride(task_queue="attacker")
    assert override.temporal_namespace is None
