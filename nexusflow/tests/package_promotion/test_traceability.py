"""Immutable optional evidence and scoped, bounded deployment metadata."""

# ruff: noqa: F811

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts import Scope
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationRecord,
    ActivationRequest,
    DeploymentEvidence,
)
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.tests.package_promotion.test_activation import SCOPE, request, system  # noqa: F401


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_explicit_request_schema_in_both_modes(mode):
    schema = ActivationRequest.model_json_schema(mode=mode)
    assert set(schema["properties"]) == {
        "project_id",
        "release_id",
        "configuration_revision",
        "operation_id",
        "expected_revision",
        "runtime_version",
        "action",
        "evidence",
    }
    assert schema["additionalProperties"] is False
    assert "DeploymentEvidence" in schema["$defs"]


@pytest.mark.parametrize("field,limit", [("change_request_number", 128), ("notes", 2000)])
def test_evidence_validation(field, limit):
    for value in ("", " \n\t", "x" * (limit + 1), 1):
        with pytest.raises(ValidationError):
            DeploymentEvidence.model_validate({field: value})
    assert getattr(DeploymentEvidence.model_validate({field: " spaced "}), field) == " spaced "
    with pytest.raises(ValidationError):
        DeploymentEvidence.model_validate({"actor_id": "injected"})


def test_legacy_serialization_and_receipt_retry(system):
    service, _, a, _, _ = system
    activation = request(a)
    old_payload = {
        "project_id": "hr",
        "release_id": a.release_id,
        "configuration_revision": "cfg-1",
        "operation_id": "activate-a",
        "expected_revision": 0,
        "runtime_version": "proof.v1",
        "action": "activate",
    }
    assert activation.model_dump(mode="json") == old_payload
    assert activation.model_dump_json() == json.dumps(old_payload, separators=(",", ":"))
    assert "evidence" not in json.loads(activation.model_dump_json())
    first = service.apply(SCOPE, activation)
    # Simulate pre-increment rows, which did not have the evidence field.
    legacy = json.loads(TypeAdapter(ActivationRecord).dump_json(first))
    assert "evidence" not in legacy["request"]
    legacy["request"].pop("evidence", None)
    service.repository._db.execute(
        "UPDATE activation_operations SET request=?,record=?",
        (canonical(old_payload), json.dumps(legacy)),
    )
    service.repository._db.commit()
    assert service.apply(SCOPE, activation) == first
    assert service.operation(SCOPE, "hr", "activate-a").evidence is None
    assert len(service.events(SCOPE, "hr")) == 1


def test_evidence_restart_retry_rollback_and_audit(system):
    service, _, a, b, path = system
    evidence = DeploymentEvidence(change_request_number=" CHG-42 ", notes="Approved offline")
    activation = request(a, evidence=evidence)
    first = service.apply(SCOPE, activation)
    assert json.loads(TypeAdapter(ActivationRecord).dump_json(first))["request"][
        "evidence"
    ] == evidence.model_dump(mode="json")
    restarted = SQLiteActivationRepository(path / "activations.db")
    try:
        assert restarted.receipt(SCOPE, activation) == first
        assert restarted.operation(SCOPE, "hr", "activate-a").evidence == evidence
    finally:
        restarted.close()
    assert service.apply(SCOPE, activation) == first
    with pytest.raises(ActivationError, match="Operation ID") as error:
        service.apply(SCOPE, request(a, evidence=DeploymentEvidence(notes="different")))
    assert error.value.code == "conflict"
    service.apply(SCOPE, request(b, 1, "activate-b", evidence=DeploymentEvidence()))
    rollback = service.apply(
        SCOPE, request(a, 2, "rollback-a", action="rollback", evidence=evidence)
    )
    assert rollback.previous_release_id == b.release_id
    assert service.current(SCOPE, "hr") == rollback
    assert service.operation(SCOPE, "hr", "activate-b").evidence == DeploymentEvidence()
    assert "Approved offline" not in repr(service.events(SCOPE, "hr"))


def test_keyset_history_concurrent_insert_scope_and_metadata_only(system):
    service, _, a, b, _ = system
    service.apply(SCOPE, request(a))
    service.apply(SCOPE, request(b, 1, "second"))
    statements = []
    service.repository._db.set_trace_callback(statements.append)
    page = service.history_page(SCOPE, "hr", limit=1)
    service.repository._db.set_trace_callback(None)
    assert [item.revision for item in page.items] == [2]
    assert page.next_before_revision == 2
    assert len(statements) == 1
    assert "json_extract(record" in statements[0]
    assert "SELECT record" not in statements[0] and "LIMIT 2" in statements[0]
    service.apply(SCOPE, request(a, 2, "third"))
    next_page = service.history_page(SCOPE, "hr", limit=1, before_revision=2)
    assert [item.revision for item in next_page.items] == [1]
    assert next_page.next_before_revision is None
    other = Scope(organization_id="other", environment_id="dev")
    assert service.history_page(other, "hr").items == ()
    with pytest.raises(ActivationError) as error:
        service.operation(other, "hr", "activate-a")
    assert error.value.code == "not_found"
    assert "content_json" not in page.model_dump_json()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"limit": "1"},
        {"before_revision": True},
        {"before_revision": 0},
        {"before_revision": 2**63},
    ],
)
def test_bounds_and_fresh_permission_before_validation(system, kwargs):
    service, _, _, _, _ = system
    with pytest.raises(ActivationError) as error:
        service.history_page(SCOPE, "hr", **kwargs)
    assert error.value.code == "invalid_request"
    assert service.authorization.calls[-1] == (SCOPE, "run.read", "hr")
    service.authorization.denied = True
    with pytest.raises(PermissionError):
        service.history_page(SCOPE, "hr", **kwargs)
    with pytest.raises(PermissionError):
        service.operation(SCOPE, "hr", "bad/id")


def test_invalid_identifiers_and_storage_errors_are_safe(system):
    service, _, _, _, _ = system
    for read in (
        lambda: service.operation(SCOPE, "hr", "bad/id"),
        lambda: service.history_page(SCOPE, "bad/id"),
    ):
        with pytest.raises(ActivationError) as error:
            read()
        assert error.value.code == "invalid_request"
    service.repository._db.execute("DROP TABLE activation_operations")
    for read in (
        lambda: service.operation(SCOPE, "hr", "operation"),
        lambda: service.history_page(SCOPE, "hr"),
    ):
        with pytest.raises(ActivationError) as error:
            read()
        assert error.value.code == "unavailable"
        assert "activation_operations" not in str(error.value)
