"""Portable fixtures and independent contract acceptance checks."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from nexusflow.contracts.export import export_schemas
from nexusflow.contracts.validation import validate_contract

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture,contract",
    [
        ("purchasing.workflow", "workflow"),
        ("onboarding.workflow", "workflow"),
        ("project", "project"),
        ("activity-descriptor", "activity-descriptor"),
        ("request-context", "request-context"),
        ("step-event", "step-event"),
        ("error", "error"),
    ],
)
def test_fixtures_conform_to_python_and_exported_schema(fixture, contract, tmp_path):
    payload = load_fixture(fixture)
    result = validate_contract(contract, json.dumps(payload))
    assert result.issues == ()
    assert result.value is not None
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / f"{contract}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize(
    "change,code,path",
    [
        (lambda w: w.update(schema_version="99"), "literal_error", "/schema_version"),
        (lambda w: w.update(secret="private-token"), "extra_forbidden", "/secret"),
        (
            lambda w: w["nodes"][2].update(policy={"timeout_seconds": "30", "maximum_attempts": 1}),
            "int_type",
            "/nodes/2/policy/timeout_seconds",
        ),
        (lambda w: w["edges"][0].update(target_node_id="absent"), "unknown_node", "/edges/0"),
        (lambda w: w["edges"][0].update(source_port="absent"), "unknown_port", "/edges/0"),
        (lambda w: w["nodes"][2].pop("activity_ref"), "missing_activity", "/nodes/2/activity_ref"),
        (
            lambda w: w["nodes"][1].update(kind="loop"),
            "unbounded_loop",
            "/nodes/1/maximum_iterations",
        ),
        (
            lambda w: w["nodes"][1].update(maximum_iterations=2),
            "unexpected_loop_bound",
            "/nodes/1/maximum_iterations",
        ),
        (
            lambda w: w["nodes"][1]["named_ports"].update(outputs=["out", "out"]),
            "duplicate_port",
            "/nodes/1/named_ports/outputs",
        ),
        (lambda w: w["edges"].append(w["edges"][0].copy()), "duplicate_edge", "/edges/3"),
        (
            lambda w: w["edges"].append(
                {
                    "source_node_id": "notify",
                    "source_port": "out",
                    "target_node_id": "approval",
                    "target_port": "in",
                }
            ),
            "unsupported_cycle",
            "/edges",
        ),
        (lambda w: w["edges"].pop(0), "unreachable_node", "/nodes/1"),
        (lambda w: w["edges"].pop(), "no_terminal_path", "/nodes/0"),
        (
            lambda w: w["start_definitions"][0].update(node_id="approval"),
            "invalid_start",
            "/start_definitions/0/node_id",
        ),
    ],
)
def test_workflow_rejects_invalid_definitions(change, code, path):
    payload = load_fixture("purchasing.workflow")
    change(payload)
    result = validate_contract("workflow", json.dumps(payload))
    assert result.value is None
    assert (path, code) in {(issue.path, issue.code) for issue in result.issues}


def test_explicit_bounded_loop_is_accepted():
    payload = load_fixture("purchasing.workflow")
    payload["nodes"][1].update(kind="loop", maximum_iterations=10)
    assert validate_contract("workflow", json.dumps(payload)).issues == ()


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda p: p.update(activity_refs=[]), "undeclared_activity"),
        (lambda p: p["activity_refs"][0].update(version="2.0.0"), "undeclared_activity"),
        (lambda p: p["workflows"].append(p["workflows"][0].copy()), "duplicate_id"),
    ],
)
def test_project_requires_exact_dependencies_and_unique_workflows(mutation, code):
    payload = load_fixture("project")
    mutation(payload)
    result = validate_contract("project", json.dumps(payload))
    assert result.value is None
    assert code in {issue.code for issue in result.issues}


def test_data_schema_syntax_is_validated():
    payload = load_fixture("activity-descriptor")
    payload["input_schema"] = {"type": "imaginary-type"}
    result = validate_contract("activity-descriptor", json.dumps(payload))
    assert result.value is None
    assert result.issues[0].path == "/input_schema"


@pytest.mark.parametrize(
    "name,payload",
    [
        ("workflow", '{"password":"private-token",'),
        ("workflow", json.dumps({"schema_version": "private-token"})),
        ("nonexistent", "private-token"),
    ],
)
def test_errors_do_not_disclose_input_values(name, payload):
    result = validate_contract(name, payload)
    assert result.value is None
    assert result.issues
    assert "private-token" not in repr(result)
