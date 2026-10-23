"""Host-side proof compiler acceptance tests without a Temporal service."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from nexusflow.contracts import Scope, WorkflowDefinition, validate_contract
from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan
from nexusflow.modules.durable_runtime.types import ReleaseBinding


@pytest.fixture
def definition():
    path = Path(__file__).parents[1] / "fixtures" / "purchasing.workflow.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def binding():
    return ReleaseBinding("acme", "dev", "release-1", "sha256:" + "a" * 64, "config-1")


@pytest.fixture
def scope():
    return Scope(organization_id="acme", environment_id="dev")


def test_compile_pins_definition_inputs_activity_and_release(definition, binding, scope):
    inputs = {"purchase": {"amount": 42}}
    plan = compile_plan(json.dumps(definition), binding, scope, inputs=inputs)
    validated = validate_contract("workflow", json.dumps(definition)).value
    assert isinstance(validated, WorkflowDefinition)
    assert (
        plan.definition_digest
        == "sha256:"
        + hashlib.sha256(
            json.dumps(
                validated.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert plan.binding == binding
    assert plan.workflow_id == "purchase-requisition"
    assert [step.node_id for step in plan.steps] == ["start", "approval", "notify", "finish"]
    assert plan.steps[2].activity_version == "1.0.0"
    assert plan.steps[2].artifact_digest == "sha256:" + "a" * 64
    definition["nodes"][2]["activity_ref"]["version"] = "2.0.0"
    inputs["purchase"]["amount"] = 99
    assert plan.steps[2].activity_version == "1.0.0"
    assert json.loads(plan.input_json) == {"purchase": {"amount": 42}}
    with pytest.raises(FrozenInstanceError):
        plan.binding.release_id = "replacement"
    assert (
        compile_plan(json.dumps(definition), binding, scope).definition_digest
        != plan.definition_digest
    )


@pytest.mark.parametrize("field,value", [("organization_id", "other"), ("environment_id", "prod")])
def test_rejects_scope_mismatch(definition, binding, scope, field, value):
    with pytest.raises(PermissionError, match="scope"):
        compile_plan(json.dumps(definition), replace(binding, **{field: value}), scope)


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_debug_is_dev_only(definition, binding, environment):
    binding = replace(binding, environment_id=environment)
    scope = Scope(organization_id="acme", environment_id=environment)
    with pytest.raises(PermissionError, match="Dev"):
        compile_plan(json.dumps(definition), binding, scope, debug=True)


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"runtime_version": "proof.v2"}, "runtime"),
        ({"release_id": ""}, "revision"),
        ({"configuration_revision": ""}, "revision"),
        ({"release_digest": "sha256:short"}, "SHA-256"),
        ({"release_digest": "sha256:" + "z" * 64}, "SHA-256"),
    ],
)
def test_release_binding_validation(definition, binding, scope, changes, message):
    with pytest.raises(PlanError, match=message):
        compile_plan(json.dumps(definition), replace(binding, **changes), scope)


def test_breakpoints_require_debug_and_executable_node(definition, binding, scope):
    with pytest.raises(PlanError, match="debug"):
        compile_plan(json.dumps(definition), binding, scope, breakpoints=("approval",))
    for node in ("absent", "start", "finish"):
        with pytest.raises(PlanError, match="executable"):
            compile_plan(json.dumps(definition), binding, scope, debug=True, breakpoints=(node,))
    plan = compile_plan(
        json.dumps(definition), binding, scope, debug=True, breakpoints=("approval", "notify")
    )
    assert plan.debug
    assert plan.breakpoints == ("approval", "notify")


@pytest.mark.parametrize(
    "kind", ["decision", "parallel", "join", "loop", "set_data", "child_workflow"]
)
def test_rejects_unimplemented_kinds(definition, binding, scope, kind):
    definition["nodes"][1]["kind"] = kind
    if kind == "loop":
        definition["nodes"][1]["maximum_iterations"] = 5
    with pytest.raises(PlanError, match="not supported"):
        compile_plan(json.dumps(definition), binding, scope)


def test_rejects_branching(definition, binding, scope):
    definition["edges"].append(
        {
            "source_node_id": "start",
            "source_port": "out",
            "target_node_id": "notify",
            "target_port": "in",
        }
    )
    with pytest.raises(PlanError, match="Branching"):
        compile_plan(json.dumps(definition), binding, scope)


def test_rejects_error_routes(definition, binding, scope):
    definition["error_routes"] = [
        {
            "source_node_id": "approval",
            "source_port": "out",
            "target_node_id": "finish",
            "target_port": "in",
        }
    ]
    with pytest.raises(PlanError, match="Error routes"):
        compile_plan(json.dumps(definition), binding, scope)


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
@pytest.mark.parametrize("reference", ["$ref", "$dynamicRef"])
def test_rejects_nested_schema_references_without_network(
    definition, binding, scope, monkeypatch, field, reference
):
    import socket

    def forbidden_network(*args, **kwargs):
        pytest.fail("Preflight attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    definition[field] = {
        "type": "object",
        "properties": {"item": {reference: "https://example.invalid/schema"}},
    }
    with pytest.raises(PlanError, match="Referenced"):
        compile_plan(json.dumps(definition), binding, scope)


def test_rejects_invalid_input_and_schema(definition, binding, scope):
    definition["input_schema"] = {
        "type": "object",
        "required": ["amount"],
        "properties": {"amount": {"type": "integer"}},
    }
    with pytest.raises(PlanError, match="input does not match"):
        compile_plan(json.dumps(definition), binding, scope, inputs={"amount": "ten"})
    definition["input_schema"] = {"type": "not-a-type"}
    with pytest.raises(PlanError, match="Invalid workflow"):
        compile_plan(json.dumps(definition), binding, scope)


@pytest.mark.parametrize("delay", [-1, 86401, True, "10"])
def test_rejects_invalid_wait_duration(definition, binding, scope, delay):
    definition["nodes"][1].update(kind="wait", input_mapping={"seconds": delay})
    with pytest.raises(PlanError, match="Wait requires"):
        compile_plan(json.dumps(definition), binding, scope)


def test_compiles_wait_and_activity_policy(definition, binding, scope):
    definition["nodes"][1].update(kind="wait", input_mapping={"seconds": 1.5})
    definition["nodes"][2]["policy"] = {"timeout_seconds": 60, "maximum_attempts": 3}
    plan = compile_plan(json.dumps(definition), binding, scope)
    assert plan.steps[1].delay_seconds == 1.5
    assert plan.steps[2].timeout_seconds == 60
    assert plan.steps[2].maximum_attempts == 3


def test_rejects_invalid_graph(definition, binding, scope):
    definition["edges"][0]["target_node_id"] = "missing"
    with pytest.raises(PlanError, match="Invalid workflow"):
        compile_plan(json.dumps(definition), binding, scope)


def test_rejects_oversized_payloads(definition, binding, scope):
    with pytest.raises(PlanError, match="input exceeds"):
        compile_plan(json.dumps(definition), binding, scope, inputs={"large": "x" * 65536})
    definition["nodes"][2]["input_mapping"] = {"large": "x" * 131072}
    with pytest.raises(PlanError, match="Definition exceeds"):
        compile_plan(json.dumps(definition), binding, scope)


def test_rejects_more_than_one_hundred_nodes(definition, binding, scope):
    template = definition["nodes"][1]
    nodes = [definition["nodes"][0]]
    nodes += [dict(template, node_id=f"step-{index}") for index in range(99)]
    nodes.append(definition["nodes"][-1])
    definition["nodes"] = nodes
    definition["edges"] = [
        {
            "source_node_id": left["node_id"],
            "source_port": "out",
            "target_node_id": right["node_id"],
            "target_port": "in",
        }
        for left, right in zip(nodes, nodes[1:], strict=False)
    ]
    with pytest.raises(PlanError, match="100 nodes"):
        compile_plan(json.dumps(definition), binding, scope)
