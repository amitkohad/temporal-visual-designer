"""Conservative activity mapping checks without expression evaluation or schema I/O."""

from collections import deque
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, WorkflowDefinition
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.designer_forms.types import Binding, DescriptorCatalog
from nexusflow.modules.project_versioning.types import ProjectContent


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _contains(value: Any, keys: set[str]) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if keys.intersection(current):
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _dominators(workflow: WorkflowDefinition) -> dict[str, set[str]]:
    nodes = {node.node_id for node in workflow.nodes}
    parents: dict[str, set[str]] = {node: set() for node in nodes}
    children: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in (*workflow.edges, *workflow.error_routes):
        if edge.source_node_id in nodes and edge.target_node_id in nodes:
            parents[edge.target_node_id].add(edge.source_node_id)
            children[edge.source_node_id].add(edge.target_node_id)
    indegree = {node: len(values) for node, values in parents.items()}
    pending = deque(sorted(node for node in nodes if not indegree[node]))
    declared_starts = {start.node_id for start in workflow.start_definitions}
    result: dict[str, set[str]] = {}
    while pending:
        node = pending.popleft()
        inherited = [result[parent] for parent in parents[node]]
        # A declared start is another entry even if a malformed edge enters it.
        result[node] = (
            {node}
            if node in declared_starts or not inherited
            else set.intersection(*inherited) | {node}
        )
        for child in sorted(children[node]):
            indegree[child] -= 1
            if not indegree[child]:
                pending.append(child)
    return result if len(result) == len(nodes) else {}


def _source_schema(schema: dict[str, Any], pointer: str) -> dict[str, Any] | None:
    if not pointer:
        return schema
    current = schema
    for raw in pointer.split("/")[1:]:
        # Reject malformed JSON Pointer escapes; arrays are deliberately unsupported.
        if "~" in raw.replace("~0", "").replace("~1", ""):
            return None
        key = raw.replace("~1", "/").replace("~0", "~")
        if current.get("type") != "object" or key not in current.get("required", []):
            return None
        properties = current.get("properties", {})
        if key not in properties or not isinstance(properties[key], dict):
            return None
        current = properties[key]
    return current


def _failed_output_can_reach(workflow: WorkflowDefinition, producer: str, consumer: str) -> bool:
    """A failed invocation does not produce a successful output, even if it dominates."""
    adjacency: dict[str, set[str]] = {}
    for edge in (*workflow.edges, *workflow.error_routes):
        adjacency.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
    pending = [
        edge.target_node_id for edge in workflow.error_routes if edge.source_node_id == producer
    ]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == consumer:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(adjacency.get(current, set()) - visited)
    return False


def _compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    if source == target:
        return True
    annotations = {
        "type",
        "title",
        "description",
        "default",
        "examples",
        "readOnly",
        "writeOnly",
        "$schema",
    }
    if set(target) - annotations:
        return False
    if "type" not in target:
        return not target or set(target) <= annotations - {"type"}
    source_type, target_type = source.get("type"), target["type"]
    if source_type is None:
        return False
    source_types = {source_type} if isinstance(source_type, str) else set(source_type)
    target_types = {target_type} if isinstance(target_type, str) else set(target_type)
    if "number" in target_types:
        target_types.add("integer")
    return source_types <= target_types


def validate_mappings(
    content: ProjectContent, catalog: DescriptorCatalog
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    descriptors: dict[tuple[str, str, str], ActivityDescriptor | None] = {}

    def issue(path: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(path, code, message))

    def descriptor(ref: ActivityRef, path: str) -> ActivityDescriptor | None:
        key = (ref.activity_id, ref.version, ref.artifact_digest)
        if key not in descriptors:
            try:
                found = catalog.descriptor(ref)
            except OSError:
                issue(path, "catalog_unavailable", "Activity catalog is unavailable")
                return None
            if found is None or found.activity_ref != ref:
                issue(path, "incompatible_activity", "Pinned activity descriptor is unavailable")
                descriptors[key] = None
            elif _contains(found.input_schema, {"$ref", "$dynamicRef"}) or _contains(
                found.output_schema, {"$ref", "$dynamicRef"}
            ):
                issue(
                    path,
                    "unsupported_schema_reference",
                    "Referenced schemas require an offline schema registry",
                )
                descriptors[key] = None
            else:
                descriptors[key] = found
        return descriptors.get(key)

    for wi, workflow in enumerate(content.workflows):
        base = f"/workflows/{wi}"
        dominators = _dominators(workflow)
        nodes = {node.node_id: node for node in workflow.nodes}
        inputs_safe = not _contains(workflow.input_schema, {"$ref", "$dynamicRef"})
        for ni, node in enumerate(workflow.nodes):
            if node.kind != "activity" or node.activity_ref is None:
                continue
            path = f"{base}/nodes/{ni}"
            activity = descriptor(node.activity_ref, path + "/activity_ref")
            if activity is None:
                continue
            schema: dict[str, Any] = dict(activity.input_schema)
            mapping = node.input_mapping
            has_binding = _contains(mapping, {"$binding"})
            if not has_binding:
                for error in Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(mapping):
                    location = "/".join(_escape(str(part)) for part in error.absolute_path)
                    issue(
                        path + "/input_mapping" + ("/" + location if location else ""),
                        "invalid_activity_input",
                        "Activity input does not satisfy its schema",
                    )
                continue
            supported = {
                "type",
                "properties",
                "required",
                "additionalProperties",
                "title",
                "description",
                "$schema",
            }
            if schema.get("type") != "object" or set(schema) - supported:
                issue(
                    path + "/input_mapping",
                    "unsupported_mapping_schema",
                    "Bound inputs require a simple object input schema",
                )
                continue
            for required in schema.get("required", []):
                if required not in mapping:
                    issue(
                        path + "/input_mapping/" + _escape(required),
                        "missing_activity_input",
                        "Required activity input is missing",
                    )
            properties = schema.get("properties", {})
            for field, value in mapping.items():
                field_path = path + "/input_mapping/" + _escape(field)
                target = properties.get(field, schema.get("additionalProperties", True))
                if target is False:
                    issue(
                        field_path, "unknown_activity_input", "Activity does not accept this input"
                    )
                    continue
                if isinstance(value, dict) and "$binding" in value:
                    try:
                        if set(value) != {"$binding"}:
                            raise ValueError
                        binding = Binding.model_validate(value["$binding"])
                    except (ValidationError, ValueError):
                        issue(field_path, "invalid_binding", "Invalid versioned variable binding")
                        continue
                    source_schema: dict[str, Any] | None = None
                    if binding.source == "input":
                        if binding.node_id is not None or not inputs_safe:
                            issue(
                                field_path,
                                "invalid_binding_source",
                                "Workflow input binding has an unsupported source",
                            )
                            continue
                        source_schema = _source_schema(workflow.input_schema, binding.path)
                    else:
                        source_node = nodes.get(binding.node_id or "")
                        if (
                            source_node is None
                            or source_node.node_id == node.node_id
                            or source_node.node_id not in dominators.get(node.node_id, set())
                            or _failed_output_can_reach(workflow, source_node.node_id, node.node_id)
                            or source_node.kind != "activity"
                            or source_node.activity_ref is None
                        ):
                            issue(
                                field_path,
                                "unavailable_output",
                                "Step output is not available on every incoming path",
                            )
                            continue
                        producer = descriptor(source_node.activity_ref, field_path)
                        if producer is not None:
                            source_schema = _source_schema(producer.output_schema, binding.path)
                    if source_schema is None:
                        issue(
                            field_path,
                            "unavailable_field",
                            "Variable field is missing or is not guaranteed to exist",
                        )
                    elif target is not True and (
                        not isinstance(target, dict) or not _compatible(source_schema, target)
                    ):
                        issue(
                            field_path,
                            "incompatible_binding_type",
                            "Variable type cannot be proven compatible with the activity input",
                        )
                elif _contains(value, {"$binding"}):
                    issue(
                        field_path,
                        "invalid_binding",
                        "Variable bindings must occupy a complete input field",
                    )
                else:
                    for _error in Draft202012Validator(
                        target, format_checker=FormatChecker()
                    ).iter_errors(value):
                        issue(
                            field_path,
                            "invalid_activity_input",
                            "Activity input does not satisfy its schema",
                        )
    return tuple(issues)
