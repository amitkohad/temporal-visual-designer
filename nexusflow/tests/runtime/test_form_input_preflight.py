"""Input-bound approval configuration keeps declarations immutable and starts strict."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.preflight import (
    PlanError,
    compile_plan,
    validate_plan_structure,
)
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.package_promotion.readiness import ProofActivationReadiness
from nexusflow.modules.package_promotion.types import ActivationRequest
from nexusflow.modules.project_versioning.types import Release

SCOPE = Scope(organization_id="acme", environment_id="dev")
BINDING = ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config")


def definition():
    value = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "purchasing.workflow.json").read_text()
    )
    value["input_schema"] = {
        "type": "object",
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
        "additionalProperties": False,
    }
    value["nodes"][1]["input_mapping"] = {
        "human_task": {
            "rule": "any_one",
            "user_ids": ["alice"],
            "form": {
                "definition": {
                    "schema_version": "0.1",
                    "form_id": "approval-form",
                    "data_schema": {
                        "type": "object",
                        "properties": {"amount": {"type": "integer"}},
                        "additionalProperties": False,
                    },
                },
                "initial_values": {
                    "amount": {
                        "$binding": {"schema_version": "0.1", "source": "input", "path": "/amount"}
                    }
                },
            },
        }
    }
    return value


def compile_value(value, inputs):
    return compile_plan(json.dumps(value), BINDING, SCOPE, inputs=inputs)


def test_materialization_changes_only_pinned_approval_values():
    value = definition()
    first = compile_value(value, {"amount": 7})
    second = compile_value(value, {"amount": 9})
    assert first.definition_digest == second.definition_digest
    assert first.steps[1].input_json == second.steps[1].input_json
    assert "$binding" in first.steps[1].input_json
    assert json.loads(first.steps[1].task_approval_json)["form"]["initial_values"] == {"amount": 7}
    assert json.loads(second.steps[1].task_approval_json)["form"]["initial_values"] == {"amount": 9}
    assert first.steps[1].task_approval_json != second.steps[1].task_approval_json
    assert value == definition()


@pytest.mark.parametrize("inputs", [{}, {"amount": "secret-invalid"}, {"amount": True}])
def test_actual_inputs_required_and_error_does_not_expose_business_data(inputs):
    with pytest.raises(PlanError) as error:
        compile_value(definition(), inputs)
    assert "secret-invalid" not in str(error.value)


def test_structure_validates_mapping_without_inventing_required_business_input():
    validate_plan_structure(json.dumps(definition()), BINDING, SCOPE)
    with pytest.raises(PlanError):
        compile_value(definition(), {})


@pytest.mark.parametrize("path", ["/missing", "", "/amount/child"])
def test_structure_and_starts_reject_invalid_source_path(path):
    value = definition()
    value["nodes"][1]["input_mapping"]["human_task"]["form"]["initial_values"]["amount"][
        "$binding"
    ]["path"] = path
    with pytest.raises(PlanError):
        validate_plan_structure(json.dumps(value), BINDING, SCOPE)
    with pytest.raises(PlanError):
        compile_value(value, {"amount": 7})


def test_unrelated_activity_binding_remains_unsupported():
    value = definition()
    value["nodes"][2]["input_mapping"] = {
        "amount": {"$binding": {"schema_version": "0.1", "source": "input", "path": "/amount"}}
    }
    with pytest.raises(PlanError):
        compile_value(value, {"amount": 7})


def test_literal_and_no_form_configuration_retain_canonical_bytes():
    value = definition()
    config = value["nodes"][1]["input_mapping"]["human_task"]
    config["form"]["initial_values"] = {"amount": 7}
    assert compile_value(value, {"amount": 9}).steps[1].task_approval_json == (
        compile_value(value, {"amount": 7}).steps[1].task_approval_json
    )
    del config["form"]
    assert compile_value(value, {"amount": 7}).steps[1].task_approval_json == (
        '{"group_ids":[],"rule":"any_one","stages":[],"user_ids":["alice"]}'
    )


@pytest.mark.parametrize("invalid_source", [False, True])
def test_readiness_checks_original_source_schema_without_business_values(invalid_source):
    value = definition()
    if invalid_source:
        value["input_schema"]["required"] = []
    configurations = Mock()
    configurations.supports.return_value = True
    readiness = ProofActivationReadiness(Mock(), Mock(), configurations)
    release = Release(
        SCOPE,
        "project",
        "release",
        1,
        json.dumps({"schema_version": "0.1", "workflows": [value]}),
        "[]",
        "sha256:" + "a" * 64,
        "sha256:" + "a" * 64,
        "author",
        "now",
    )
    request = ActivationRequest(
        project_id="project",
        release_id="release",
        configuration_revision="config",
        operation_id="activate",
        expected_revision=0,
    )
    issues = readiness.validate(SCOPE, release, request)
    assert bool(issues) is invalid_source
    if invalid_source:
        assert any(issue.code == "unsupported_workflow" for issue in issues)
