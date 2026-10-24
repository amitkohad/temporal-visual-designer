"""Host-side compilation of the deliberately limited runtime-proof subset."""

import hashlib
import json
import math

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from pydantic import JsonValue

from nexusflow.contracts import Scope, WorkflowDefinition, validate_contract
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding, Step

MAX_DEFINITION_BYTES = 131072


class PlanError(ValueError):
    """A definition cannot be executed by this proof interpreter."""


def compile_plan(
    definition_json: str,
    binding: ReleaseBinding,
    authorized_scope: Scope,
    *,
    inputs: dict[str, JsonValue] | None = None,
    debug: bool = False,
    breakpoints: tuple[str, ...] = (),
) -> ExecutionPlan:
    if (binding.organization_id, binding.environment_id) != (
        authorized_scope.organization_id,
        authorized_scope.environment_id,
    ):
        raise PermissionError("Execution scope does not match the authorized scope")
    if binding.runtime_version != "proof.v1":
        raise PlanError("Unsupported runtime version")
    if not binding.release_id or not binding.configuration_revision:
        raise PlanError("Release and configuration revision are required")
    if len(binding.release_digest) != 71 or not binding.release_digest.startswith("sha256:"):
        raise PlanError("Release requires a SHA-256 digest")
    try:
        int(binding.release_digest[7:], 16)
    except ValueError:
        raise PlanError("Release requires a SHA-256 digest") from None
    if debug and binding.environment_id != "dev":
        raise PermissionError("Debug runs are restricted to Dev")
    if breakpoints and not debug:
        raise PlanError("Breakpoints require debug mode")
    result = validate_contract("workflow", definition_json)
    if not isinstance(result.value, WorkflowDefinition):
        raise PlanError("Invalid workflow definition")
    definition = result.value
    if len(definition.nodes) > 100 or len(definition.start_definitions) != 1:
        raise PlanError("Proof supports at most 100 nodes and one start")
    if definition.error_routes:
        raise PlanError("Error routes are not supported by this proof")
    # The authoring binding contract is not executable by proof.v1. Never pass
    # reserved syntax through as a literal activity argument.
    pending_values: list[JsonValue] = [node.input_mapping for node in definition.nodes]
    while pending_values:
        value = pending_values.pop()
        if isinstance(value, dict):
            if "$binding" in value:
                raise PlanError("Variable bindings are not supported by this proof")
            pending_values.extend(value.values())
        elif isinstance(value, list):
            pending_values.extend(value)

    # External schema references must never introduce network I/O during preflight.
    def has_reference(value: JsonValue) -> bool:
        if isinstance(value, dict):
            return (
                "$ref" in value
                or "$dynamicRef" in value
                or any(has_reference(v) for v in value.values())
            )
        return isinstance(value, list) and any(has_reference(v) for v in value)

    if has_reference(definition.input_schema) or has_reference(definition.output_schema):
        raise PlanError("Referenced data schemas are not supported by this proof")
    try:
        input_json = json.dumps(
            inputs if inputs is not None else {}, allow_nan=False, sort_keys=True
        )
        Draft202012Validator(definition.input_schema).validate(json.loads(input_json))
    except (ValueError, SchemaValidationError):
        raise PlanError("Workflow input does not match its schema") from None
    if len(input_json.encode()) > 65536:
        raise PlanError("Workflow input exceeds the proof payload limit")
    nodes = {node.node_id: node for node in definition.nodes}
    successors: dict[str, str] = {}
    incoming: set[str] = set()
    for edge in definition.edges:
        if edge.source_node_id in successors or edge.target_node_id in incoming:
            raise PlanError("Branching and joins require the full interpreter")
        successors[edge.source_node_id] = edge.target_node_id
        incoming.add(edge.target_node_id)
    steps: list[Step] = []
    node_id = definition.start_definitions[0].node_id
    while True:
        node = nodes[node_id]
        if node.kind not in {"start", "end", "activity", "approval", "wait"}:
            raise PlanError("Node kind is not supported by this proof")
        delay = node.input_mapping.get("seconds", 0)
        delay_seconds = 0.0
        if node.kind == "wait" and (
            isinstance(delay, bool)
            or not isinstance(delay, int | float)
            or not math.isfinite(delay)
            or not 0 <= delay <= 86400
        ):
            raise PlanError("Wait requires finite seconds between 0 and 86400")
        if node.kind == "wait":
            assert isinstance(delay, int | float)
            delay_seconds = float(delay)
        if node.kind != "start" and node.named_ports.inputs != ("in",):
            raise PlanError("Proof steps require one named input port: in")
        if node.kind != "end" and node.named_ports.outputs != ("out",):
            raise PlanError("Proof steps require one named output port: out")
        ref = node.activity_ref
        policy = node.policy
        steps.append(
            Step(
                node_id=node.node_id,
                kind=node.kind,
                input_json=json.dumps(node.input_mapping, sort_keys=True, allow_nan=False),
                activity_id=ref.activity_id if ref else "",
                activity_version=ref.version if ref else "",
                artifact_digest=ref.artifact_digest if ref else "",
                timeout_seconds=policy.timeout_seconds if policy else 30,
                maximum_attempts=policy.maximum_attempts if policy else 1,
                delay_seconds=delay_seconds,
            )
        )
        if node_id not in successors:
            if node.kind != "end":
                raise PlanError("Proof workflow must finish at an end node")
            break
        node_id = successors[node_id]
    if len(steps) != len(nodes):
        raise PlanError("Proof workflow must be a single connected sequence")
    executable_ids = {step.node_id for step in steps if step.kind not in {"start", "end"}}
    if any(node_id not in executable_ids for node_id in breakpoints):
        raise PlanError("Breakpoint must reference an executable step")
    canonical = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(canonical.encode()) > MAX_DEFINITION_BYTES:
        raise PlanError("Definition exceeds the proof payload limit")
    return ExecutionPlan(
        binding=binding,
        workflow_id=definition.workflow_id,
        definition_digest="sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        steps=tuple(steps),
        input_json=input_json,
        debug=debug,
        breakpoints=breakpoints,
    )
