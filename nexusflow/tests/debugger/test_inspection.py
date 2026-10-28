"""Pure input inspection never exposes unclassified or protected input values."""

import json
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.inspection import MAX_RESPONSE_BYTES, inspect_inputs
from nexusflow.modules.designer_debugger.types import (
    DebugError,
    DebugRecord,
    DebugSession,
    DebugStart,
)
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding
from nexusflow.modules.identity_environments.types import Environment


def record(values):
    request = DebugStart(
        operation_id="inspect",
        source_revision=1,
        workflow_id="example",
        configuration_revision="cfg-1",
        input=values,
        mode="real",
    )
    scope = Scope(organization_id="acme", environment_id="dev")
    session = DebugSession(
        session_id="debug-1",
        project_id="project",
        source_revision=1,
        workflow_id="example",
        configuration_revision="cfg-1",
        definition_digest="sha256:" + "a" * 64,
        actor_id="owner",
        created_at="2026-10-26T00:00:00Z",
        execution_id="nf-" + "b" * 32,
        temporal_run_id="run-1",
        state="started",
    )
    plan = ExecutionPlan(
        ReleaseBinding("acme", "dev", "debug-1", "sha256:" + "c" * 64, "cfg-1"),
        "example",
        session.definition_digest,
        (),
        input_json=json.dumps(values),
        debug=True,
    )
    return DebugRecord(request, session, Environment(scope, "acme-dev", "queue"), plan)


def schema(properties):
    return {"type": "object", "properties": properties}


def public(kind="string", **extra):
    return {"type": kind, "x-nexusflow-inspect": True, **extra}


def test_public_primitives_only_and_input_snapshot_unchanged():
    frozen = record(
        {
            "name": "Alice",
            "count": 3,
            "active": True,
            "empty": None,
            "amount": 1.5,
            "private": "hidden-marker",
            "dynamic-secret-key": "hidden",
        }
    )
    before = frozen.request.model_dump_json(), frozen.plan.input_json
    result = inspect_inputs(
        frozen,
        schema(
            {
                "name": public(),
                "count": public("integer"),
                "active": public("boolean"),
                "empty": public("null"),
                "amount": public("number"),
                "private": {"type": "string"},
            }
        ),
    )
    assert result.values == {
        "name": "Alice",
        "count": 3,
        "active": True,
        "empty": None,
        "amount": 1.5,
    }
    assert result.redacted_paths == ("", "/private")
    assert not result.truncated
    assert "hidden-marker" not in result.model_dump_json()
    assert "dynamic-secret-key" not in result.model_dump_json()
    assert before == (frozen.request.model_dump_json(), frozen.plan.input_json)


@pytest.mark.parametrize(
    "mark",
    [
        {"writeOnly": True},
        {"x-sensitive": True},
        {"x-secret": True},
        {"format": "password"},
        {"format": "PASSWORD"},
        {"writeOnly": "false"},
        {"x-sensitive": None},
    ],
)
def test_sensitive_flags_override_public_and_nested_subtrees(mark):
    result = inspect_inputs(
        record({"protected": {"child": "secret-sentinel"}}),
        schema(
            {
                "protected": {
                    "type": "object",
                    "properties": {"child": public()},
                    "x-nexusflow-inspect": True,
                    **mark,
                },
            }
        ),
    )
    assert result.values == {}
    assert result.redacted_paths == ("/protected",)
    assert "secret-sentinel" not in result.model_dump_json()


@pytest.mark.parametrize(
    "keyword",
    [
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "dependentSchemas",
        "dependencies",
        "patternProperties",
        "unevaluatedProperties",
    ],
)
def test_unsupported_schema_constructs_fail_closed_without_resolution(keyword):
    result = inspect_inputs(
        record({"field": "secret"}),
        schema(
            {
                "field": public(**{keyword: "https://must-never-resolve.invalid/schema"}),
            }
        ),
    )
    assert result.values == {}
    assert result.redacted_paths == ("/field",)


def test_objects_require_individual_leaf_opt_in_arrays_and_unknowns_stay_hidden():
    result = inspect_inputs(
        record(
            {
                "nested": {"visible": "ok", "hidden": "secret", "runtime-secret-key": "value"},
                "items": ["array-secret"],
            }
        ),
        schema(
            {
                "nested": {
                    "type": "object",
                    "x-nexusflow-inspect": True,
                    "properties": {"visible": public(), "hidden": {"type": "string"}},
                },
                "items": public("array", items=public()),
            }
        ),
    )
    assert result.values == {"nested": {"visible": "ok"}}
    assert result.redacted_paths == ("/items", "/nested", "/nested/hidden")
    assert "runtime-secret-key" not in result.model_dump_json()
    assert "array-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "field_schema,value",
    [
        ({}, "secret"),
        (True, "secret"),
        (public(["string", "null"]), "secret"),
        (public("integer"), True),
        (public("integer"), 3.0),
        (public("number"), False),
        (public("boolean"), 1),
        ({"type": "string", "x-nexusflow-inspect": 1}, "secret"),
    ],
)
def test_malformed_unclassified_or_mismatched_fields_are_not_coerced(field_schema, value):
    result = inspect_inputs(record({"field": value}), schema({"field": field_schema}))
    assert result.values == {}
    assert result.redacted_paths == ("/field",)


def test_default_schema_redacts_everything_and_absent_fields_are_not_reported():
    result = inspect_inputs(record({"unknown": "secret"}), {})
    assert result.values == {} and result.redacted_paths == ("",)
    assert inspect_inputs(record({}), schema({"missing": public()})).redacted_paths == ()


def test_json_pointer_paths_are_sorted_and_escaped():
    result = inspect_inputs(record({"z/~": "secret", "a": "secret"}), schema({"z/~": {}, "a": {}}))
    assert result.redacted_paths == ("/a", "/z~1~0")


def test_scalar_limit_counts_encoded_utf8_and_redacts_instead_of_partial_value():
    good = inspect_inputs(record({"field": "a" * 1022}), schema({"field": public()}))
    assert len(good.values["field"]) == 1022
    for value in ("a" * 1023, "é" * 512):
        result = inspect_inputs(record({"field": value}), schema({"field": public()}))
        assert result.values == {} and result.truncated
        assert value not in result.model_dump_json()


def test_response_aggregate_budget_and_large_declared_paths_fail_closed():
    values = {f"field-{index}": "x" * 900 for index in range(30)}
    result = inspect_inputs(record(values), schema({key: public() for key in values}))
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated
    assert len(result.model_dump_json().encode()) <= MAX_RESPONSE_BYTES
    name = "n" * 17000
    result = inspect_inputs(record({name: "secret"}), schema({name: {}}))
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated
    assert name not in result.model_dump_json()


def test_depth_and_visit_budgets_stop_expansion():
    value = "secret"
    declaration = public()
    for _ in range(10):
        value = {"child": value}
        declaration = {"type": "object", "properties": {"child": declaration}}
    result = inspect_inputs(record(value), declaration)
    assert result.truncated and "secret" not in result.model_dump_json()
    values = {f"f-{index}": index for index in range(1002)}
    result = inspect_inputs(record(values), schema({key: public("integer") for key in values}))
    assert result.truncated
    assert len(result.values) < 1000
    assert len(result.model_dump_json().encode()) <= MAX_RESPONSE_BYTES


def test_redacted_path_budget_never_exposes_unknown_names():
    values = {f"hidden-{index}": "secret" for index in range(102)}
    result = inspect_inputs(record(values), schema({key: {} for key in values}))
    assert result.values == {} and result.redacted_paths == ("",) and result.truncated


@pytest.mark.parametrize("encoded", ["[]", "not-json", '{"field":NaN}', '{"field":"changed"}'])
def test_invalid_or_mismatched_frozen_inputs_return_safe_error(encoded):
    frozen = record({"field": "sensitive-sentinel"})
    with pytest.raises(DebugError) as error:
        inspect_inputs(
            replace(frozen, plan=replace(frozen.plan, input_json=encoded)),
            schema({"field": public()}),
        )
    assert error.value.code == "unavailable"
    assert "sensitive-sentinel" not in str(error.value)


def test_oversized_frozen_input_fails_safely():
    frozen = record({"field": "secret-sentinel" * 5000})
    with pytest.raises(DebugError) as error:
        inspect_inputs(frozen, schema({"field": public()}))
    assert "secret-sentinel" not in str(error.value)
