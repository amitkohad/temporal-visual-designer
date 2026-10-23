"""Mapping proof boundaries and offline schema validation."""

import json

import pytest
from jsonschema import Draft202012Validator

from nexusflow.modules.designer_forms.types import Binding
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.tests.designer_forms.test_acceptance import Catalog, binding, project


class Schemas(Catalog):
    def __init__(self, input_schema=None, output_schema=None):
        super().__init__()
        self.input_schema = input_schema
        self.output_schema = output_schema

    def descriptor(self, reference):
        descriptor = super().descriptor(reference)
        updates = {}
        if self.input_schema is not None:
            updates["input_schema"] = self.input_schema
        if self.output_schema is not None:
            updates["output_schema"] = self.output_schema
        return descriptor.model_copy(update=updates)


def validate(payload, catalog=None):
    snapshot = ProjectContent.model_validate_json(json.dumps(payload))
    return AuthoringValidator(catalog or Catalog()).validate(snapshot)


@pytest.mark.parametrize("pointer", ["/missing", "/employee~2", "/employee/name"])
def test_missing_or_malformed_field_path_is_located(pointer):
    issues = validate(project({"name": binding("input", pointer)}))
    assert any(
        issue.code == "unavailable_field" and issue.path.endswith("/name") for issue in issues
    )


def test_json_pointer_escaped_property_is_supported():
    payload = project({"name": binding("input", "/employee~1name~0")})
    payload["workflows"][0]["input_schema"] = {
        "type": "object",
        "properties": {"employee/name~": {"type": "string"}},
        "required": ["employee/name~"],
    }
    assert validate(payload) == ()


@pytest.mark.parametrize(
    "updates",
    [{"schema_version": "9.0"}, {"source": "python"}, {"node_id": "start"}, {"extra": "bad"}],
)
def test_unknown_or_malformed_binding_cannot_be_literal(updates):
    payload = project()
    payload["workflows"][0]["nodes"][1]["input_mapping"]["name"]["$binding"].update(updates)
    assert validate(payload)


def test_integer_can_bind_to_number_but_not_reverse():
    target = {"type": "object", "properties": {"name": {"type": "number"}}, "required": ["name"]}
    payload = project()
    payload["workflows"][0]["input_schema"]["properties"]["employee"] = {"type": "integer"}
    assert validate(payload, Schemas(target)) == ()
    payload["workflows"][0]["input_schema"]["properties"]["employee"] = {"type": "number"}
    target["properties"]["name"] = {"type": "integer"}
    assert any(
        issue.code == "incompatible_binding_type" for issue in validate(payload, Schemas(target))
    )


def test_nullable_source_cannot_bind_to_nonnullable_target():
    payload = project()
    payload["workflows"][0]["input_schema"]["properties"]["employee"] = {"type": ["string", "null"]}
    assert any(issue.code == "incompatible_binding_type" for issue in validate(payload))


def test_target_constraint_requires_proof_not_just_same_primitive_type():
    target = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 5}},
        "required": ["name"],
    }
    assert any(
        issue.code == "incompatible_binding_type" for issue in validate(project(), Schemas(target))
    )


def test_literal_format_and_required_constraints_are_checked():
    target = {
        "type": "object",
        "properties": {"name": {"type": "string", "format": "email"}},
        "required": ["name"],
    }
    assert any(
        issue.code == "invalid_activity_input"
        for issue in validate(project({"name": "invalid"}), Schemas(target))
    )
    assert any(
        issue.code == "invalid_activity_input"
        for issue in validate(project({"other": "value"}), Schemas(target))
    )


def test_schema_references_fail_offline_before_any_resolver_can_run():
    schema = {"$ref": "https://must-not-be-fetched.invalid/schema.json"}
    issues = validate(project(), Schemas(schema))
    assert any(issue.code == "unsupported_schema_reference" for issue in issues)


def test_bound_input_schema_composition_is_rejected_until_implication_is_supported():
    schema = {
        "type": "object",
        "allOf": [{"required": ["name"]}],
        "properties": {"name": {"type": "string"}},
    }
    assert any(
        issue.code == "unsupported_mapping_schema" for issue in validate(project(), Schemas(schema))
    )


def test_malicious_literal_is_data_and_diagnostics_do_not_echo_it(tmp_path):
    marker = tmp_path / "executed.txt"
    value = f"__import__('pathlib').Path({str(marker)!r}).write_text('bad')"
    assert validate(project({"name": value})) == ()
    issues = validate(project({"name": {"text": value}}))
    assert issues and all(value not in issue.message for issue in issues)
    assert not marker.exists()


def test_binding_schema_is_portable():
    schema = Binding.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(binding("input", "/employee")["$binding"])


@pytest.mark.parametrize("chained", [False, True])
def test_failed_producer_output_unavailable_in_recovery_and_later_merge(chained):
    from nexusflow.tests.designer_forms.test_acceptance import edge, node

    payload = project()
    workflow = payload["workflows"][0]
    consumer = node("consumer", "activity", {"name": binding("step", "/account", "create")})
    workflow["nodes"].insert(-1, consumer)
    if chained:
        workflow["nodes"].insert(-1, node("recover", "set_data"))
        workflow["edges"] = [
            edge("start", "create"),
            edge("create", "consumer"),
            edge("recover", "consumer"),
            edge("consumer", "end"),
        ]
        workflow["error_routes"] = [edge("create", "recover")]
    else:
        workflow["edges"] = [
            edge("start", "create"),
            edge("create", "end"),
            edge("consumer", "end"),
        ]
        workflow["error_routes"] = [edge("create", "consumer")]
    issues = validate(payload)
    assert any(issue.code == "unavailable_output" for issue in issues)
