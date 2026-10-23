"""Supported form definition semantics are independently testable offline."""

import pytest

from nexusflow.modules.designer_forms.form_definition import validate_form_definition
from nexusflow.modules.project_versioning.types import FormDefinition


def form(**updates: object) -> FormDefinition:
    values: dict[str, object] = {
        "form_id": "request",
        "schema_version": "0.1",
        "data_schema": {
            "type": "object",
            "properties": {"amount": {"type": "number"}, "reason": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    values.update(updates)
    return FormDefinition.model_validate(values)


def test_native_conditional_rules_and_field_annotations() -> None:
    definition = form(
        visibility_rules=(
            {
                "schema_version": "0.1",
                "field_id": "reason",
                "when": {"properties": {"amount": {"minimum": 100}}, "required": ["amount"]},
            },
        ),
        validation_rules=(
            {
                "schema_version": "0.1",
                "data_schema": {
                    "if": {"properties": {"amount": {"minimum": 100}}, "required": ["amount"]},
                    "then": {"required": ["reason"]},
                },
            },
        ),
        field_permissions={"reason": {"read_only": True}},
        layout={"field_order": ["reason", "amount"]},
    )
    assert validate_form_definition(definition) == ()
    schema = dict(definition.data_schema)
    schema["properties"] = {"amount": {"type": "number", "readOnly": True, "writeOnly": False}}
    assert validate_form_definition(form(data_schema=schema)) == ()


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {
            "type": "object",
            "properties": {"bad id": {"type": "string"}},
            "additionalProperties": False,
        },
        {"type": "object", "properties": {"field": True}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"field": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
    ],
)
def test_unsupported_field_shapes_receive_located_errors(schema: dict[str, object]) -> None:
    issues = validate_form_definition(form(data_schema=schema))
    assert issues
    assert all(issue.path.startswith("/data_schema") for issue in issues)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_references_are_rejected_without_resolution(keyword: str) -> None:
    issues = validate_form_definition(
        form(
            validation_rules=(
                {
                    "schema_version": "0.1",
                    "data_schema": {"allOf": [{keyword: "https://invalid.test/schema"}]},
                },
            )
        )
    )
    assert any(issue.code == "unsupported_reference" for issue in issues)
    assert "https://" not in repr(issues)


def test_nested_annotations_are_unsupported() -> None:
    definition = form(
        data_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "readOnly": True}},
                }
            },
        }
    )
    assert any(
        issue.code == "unsupported_annotation" for issue in validate_form_definition(definition)
    )


def test_duplicate_unknown_and_invalid_rules_are_sanitized() -> None:
    definition = form(
        visibility_rules=(
            {"schema_version": "0.1", "field_id": "reason", "when": {}},
            {"schema_version": "0.1", "field_id": "reason", "when": {}},
            {"schema_version": "0.1", "field_id": "missing", "when": {}},
            {"schema_version": "wrong-secret-value", "field_id": "reason", "when": {}},
        ),
        validation_rules=(
            {"schema_version": "0.1", "data_schema": {"type": "invalid-secret-value"}},
        ),
    )
    issues = validate_form_definition(definition)
    assert {"duplicate_rule", "unknown_field", "invalid_rule"} <= {issue.code for issue in issues}
    assert "secret-value" not in repr(issues)


@pytest.mark.parametrize(
    "updates, code",
    [
        ({"field_permissions": {"amount": {"read_only": "yes"}}}, "field_permissions"),
        ({"field_permissions": {"missing": {"read_only": False}}}, "unknown_field"),
        (
            {"field_permissions": {"amount": {"read_only": False, "execute": True}}},
            "field_permissions",
        ),
        ({"layout": {"html": "<script>bad</script>"}}, "unsupported_layout"),
        ({"layout": {"field_order": ["amount", "amount"]}}, "field_order"),
        ({"layout": {"field_order": ["missing"]}}, "field_order"),
        ({"layout": {"field_order": [True]}}, "field_order"),
        ({"attachment_policy": {"enabled": True}}, "unsupported_attachments"),
    ],
)
def test_unsupported_metadata_fails_explicitly(updates: dict[str, object], code: str) -> None:
    assert code in {issue.code for issue in validate_form_definition(form(**updates))}


def test_field_and_rule_limits() -> None:
    definition = form(
        data_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {f"field{index}": {"type": "string"} for index in range(101)},
        },
        validation_rules=tuple({"schema_version": "0.1", "data_schema": {}} for _ in range(101)),
    )
    assert {"field_limit", "rule_limit"} <= {
        issue.code for issue in validate_form_definition(definition)
    }


@pytest.mark.parametrize(
    "keyword, value", [("pattern", "(a+)+$"), ("patternProperties", {"(a+)+$": {}})]
)
@pytest.mark.parametrize("location", ["field", "visibility", "validation"])
def test_user_regex_rejected_in_schemas_and_rules(
    keyword: str, value: object, location: str
) -> None:
    regex_schema = {keyword: value}
    if location == "field":
        definition = form(
            data_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"reason": {"type": "string", **regex_schema}},
            }
        )
    elif location == "visibility":
        definition = form(
            visibility_rules=(
                {"schema_version": "0.1", "field_id": "reason", "when": regex_schema},
            )
        )
    else:
        definition = form(
            validation_rules=({"schema_version": "0.1", "data_schema": regex_schema},)
        )
    issues = validate_form_definition(definition)
    assert any(issue.code == "unsupported_pattern" for issue in issues)
    assert "(a+)+$" not in repr(issues)


def test_native_format_and_pattern_named_field_supported() -> None:
    definition = form(
        data_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"pattern": {"type": "string", "format": "email"}},
        }
    )
    assert validate_form_definition(definition) == ()


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
@pytest.mark.parametrize("location", ["field", "visibility", "validation"])
def test_alternative_schema_branches_rejected(keyword: str, location: str) -> None:
    union_schema = {keyword: [{"required": ["reason"]}, {"required": ["amount"]}]}
    if location == "field":
        definition = form(
            data_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"reason": {"type": "string", **union_schema}},
            }
        )
    elif location == "visibility":
        definition = form(
            visibility_rules=(
                {"schema_version": "0.1", "field_id": "reason", "when": union_schema},
            )
        )
    else:
        definition = form(
            validation_rules=({"schema_version": "0.1", "data_schema": union_schema},)
        )
    assert any(issue.code == "unsupported_union" for issue in validate_form_definition(definition))


def test_native_conjunction_dependency_and_negation_supported() -> None:
    definition = form(
        validation_rules=(
            {
                "schema_version": "0.1",
                "data_schema": {
                    "allOf": [
                        {"not": {"properties": {"amount": {"const": -1}}, "required": ["amount"]}}
                    ],
                    "dependentRequired": {"amount": ["reason"]},
                    "dependentSchemas": {"amount": {"required": ["reason"]}},
                },
            },
        )
    )
    assert validate_form_definition(definition) == ()
