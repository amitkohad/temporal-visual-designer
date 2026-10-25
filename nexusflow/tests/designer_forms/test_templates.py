"""Tenant-neutral templates remain editable drafts and compile without providers."""

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_forms.templates import (
    TemplateAssignment,
    build_template,
    get_template,
    list_templates,
)
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.durable_runtime.preflight import compile_plan, validate_plan_structure
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.project_versioning.types import VersioningError


class EmptyCatalog:
    def descriptor(self, reference):
        raise AssertionError("Templates must not require external activities")


INPUTS = {
    "employee-onboarding": {
        "employee_name": "Ada",
        "department": "Research",
        "start_date": "2027-01-01",
    },
    "purchase-requisition": {
        "requester": "Ada",
        "item": "Laptop",
        "amount": 1500,
        "currency": "USD",
    },
}


def assignments(template):
    return {
        slot: TemplateAssignment(user_ids=(f"{slot}-user",)) for slot in template.assignment_slots
    }


@pytest.mark.parametrize("template_id", INPUTS)
def test_templates_validate_and_compile_with_real_inputs(template_id):
    template = get_template(template_id)
    content = build_template(template_id, assignments(template))
    assert AuthoringValidator(EmptyCatalog()).validate(content) == ()
    assert content.forms == content.activity_refs == content.connections == ()
    workflow = content.workflows[0]
    assert [node.node_id for node in workflow.nodes] == ["start", *template.assignment_slots, "end"]
    assert len(workflow.edges) == len(workflow.nodes) - 1
    scope = Scope(organization_id="acme", environment_id="dev")
    binding = ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config")
    validate_plan_structure(workflow.model_dump_json(), binding, scope)
    plan = compile_plan(workflow.model_dump_json(), binding, scope, inputs=INPUTS[template_id])
    approvals = [step for step in plan.steps if step.kind == "approval"]
    assert len(approvals) == len(template.assignment_slots)
    for step in approvals:
        task = json.loads(step.task_approval_json)
        assert task["form"]["initial_values"] == INPUTS[template_id]
        assert task["user_ids"] == [f"{step.node_id}-user"]
        schema = task["form"]["definition"]["data_schema"]
        for key in INPUTS[template_id]:
            assert schema["properties"][key]["readOnly"] is True
        validator = Draft202012Validator(schema)
        assert list(validator.iter_errors(INPUTS[template_id]))
        assert list(validator.iter_errors({**INPUTS[template_id], "confirmed": False}))
        validator.validate({**INPUTS[template_id], "confirmed": True})
        assert list(
            validator.iter_errors({**INPUTS[template_id], "confirmed": True, "notes": "x" * 2001})
        )


def test_fresh_metadata_and_content_do_not_mutate_catalog_or_other_instances():
    template = get_template("employee-onboarding")
    original = template.model_dump_json()
    content = build_template(template.template_id, assignments(template))
    other = build_template(template.template_id, assignments(template))
    content.workflows[0].input_schema["extra"] = True
    template.input_schema["extra"] = True
    assert get_template(template.template_id).model_dump_json() == original
    assert "extra" not in other.workflows[0].input_schema
    assert len(list_templates()) == 2


@pytest.mark.parametrize("template_id", INPUTS)
def test_assignments_and_bindings_are_explicit_and_strict(template_id):
    template = get_template(template_id)
    selected = assignments(template)
    selected["manager"] = TemplateAssignment(rule="all_approvers", group_ids=("managers",))
    content = build_template(template_id, selected)
    task = content.workflows[0].nodes[1].input_mapping["human_task"]
    assert task["rule"] == "all_approvers"
    assert task["group_ids"] == ["managers"]
    assert task["user_ids"] == []
    for key, binding in task["form"]["initial_values"].items():
        assert binding == {
            "$binding": {"schema_version": "0.1", "source": "input", "path": f"/{key}"}
        }
    for invalid in ({}, {**selected, "extra": selected["manager"]}):
        with pytest.raises(VersioningError) as error:
            build_template(template_id, invalid)
        assert error.value.code == "invalid_content"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"user_ids": ("alice", "alice")},
        {"group_ids": ("hr", "hr")},
        {"user_ids": ("alice",), "rule": "sequential"},
        {"user_ids": ("alice",), "tenant_id": "acme"},
        {"user_ids": ("invalid id",)},
        {"user_ids": ["alice"]},
    ],
)
def test_invalid_assignments_rejected(values):
    with pytest.raises(ValidationError):
        TemplateAssignment(**values)


@pytest.mark.parametrize("template_id", INPUTS)
def test_input_schemas_require_exact_context(template_id):
    schema = get_template(template_id).input_schema
    validator = Draft202012Validator(schema)
    validator.validate(INPUTS[template_id])
    assert list(validator.iter_errors({}))
    assert list(validator.iter_errors({**INPUTS[template_id], "unexpected": True}))
    for key in INPUTS[template_id]:
        invalid = 0 if key == "amount" else ""
        assert list(validator.iter_errors({**INPUTS[template_id], key: invalid}))


def test_unknown_template_has_safe_not_found():
    with pytest.raises(VersioningError, match="^Template not found$") as error:
        get_template("sensitive-marker")
    assert error.value.code == "not_found"
