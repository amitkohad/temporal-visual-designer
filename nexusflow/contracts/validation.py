"""Structural and foundation graph checks with input-free, located errors."""

from dataclasses import dataclass

from pydantic import ValidationError

from nexusflow.contracts.models import CONTRACTS, Contract, ProjectDefinition, WorkflowDefinition


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    value: Contract | None
    issues: tuple[ValidationIssue, ...]


def _pointer(location: tuple[str | int, ...]) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in location)


def validate_contract(name: str, payload: str | bytes) -> ValidationResult:
    """Validate JSON without coercion, network access or source payloads in errors."""
    model = CONTRACTS.get(name)
    if model is None:
        return ValidationResult(
            None, (ValidationIssue("", "unknown_contract", "Unknown contract"),)
        )
    try:
        value = model.model_validate_json(payload)
    except ValidationError as error:
        issues = tuple(
            ValidationIssue(_pointer(item["loc"]), item["type"], "Invalid contract field")
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        )
        return ValidationResult(None, issues)
    issues_list: list[ValidationIssue] = []
    if isinstance(value, WorkflowDefinition):
        issues_list.extend(_workflow_issues(value))
    if isinstance(value, ProjectDefinition):
        workflow_ids: set[str] = set()
        for index, workflow in enumerate(value.workflows):
            if workflow.workflow_id in workflow_ids:
                issues_list.append(
                    ValidationIssue(
                        f"/workflows/{index}/workflow_id", "duplicate_id", "Duplicate workflow ID"
                    )
                )
            workflow_ids.add(workflow.workflow_id)
            issues_list.extend(
                ValidationIssue(f"/workflows/{index}{issue.path}", issue.code, issue.message)
                for issue in _workflow_issues(workflow)
            )
        declared = {
            (ref.activity_id, ref.version, ref.artifact_digest) for ref in value.activity_refs
        }
        for wi, workflow in enumerate(value.workflows):
            for ni, node in enumerate(workflow.nodes):
                ref = node.activity_ref
                if ref and (ref.activity_id, ref.version, ref.artifact_digest) not in declared:
                    issues_list.append(
                        ValidationIssue(
                            f"/workflows/{wi}/nodes/{ni}/activity_ref",
                            "undeclared_activity",
                            "Activity must be declared by the project",
                        )
                    )
    return ValidationResult(None if issues_list else value, tuple(issues_list))


def _workflow_issues(workflow: WorkflowDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def issue(path: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(path, code, message))

    nodes = {}
    for index, node in enumerate(workflow.nodes):
        if node.node_id in nodes:
            issue(f"/nodes/{index}/node_id", "duplicate_id", "Duplicate node ID")
        nodes[node.node_id] = node
        for direction, ports in (
            ("inputs", node.named_ports.inputs),
            ("outputs", node.named_ports.outputs),
        ):
            if len(set(ports)) != len(ports):
                issue(
                    f"/nodes/{index}/named_ports/{direction}",
                    "duplicate_port",
                    "Duplicate port name",
                )
        if node.kind == "activity" and node.activity_ref is None:
            issue(
                f"/nodes/{index}/activity_ref",
                "missing_activity",
                "Activity requires a pinned reference",
            )
        if node.kind == "loop" and node.maximum_iterations is None:
            issue(
                f"/nodes/{index}/maximum_iterations",
                "unbounded_loop",
                "Loop requires an iteration bound",
            )
        if node.kind != "loop" and node.maximum_iterations is not None:
            issue(
                f"/nodes/{index}/maximum_iterations",
                "unexpected_loop_bound",
                "Only loop nodes accept iteration bounds",
            )

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    reverse: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    seen_edges: set[tuple[str, str, str, str]] = set()
    for collection, edges in (("edges", workflow.edges), ("error_routes", workflow.error_routes)):
        for index, edge in enumerate(edges):
            path = f"/{collection}/{index}"
            source = nodes.get(edge.source_node_id)
            target = nodes.get(edge.target_node_id)
            if source is None or target is None:
                issue(path, "unknown_node", "Connection references a missing node")
                continue
            if (
                edge.source_port not in source.named_ports.outputs
                or edge.target_port not in target.named_ports.inputs
            ):
                issue(path, "unknown_port", "Connection references a missing port")
                continue
            key = (edge.source_node_id, edge.source_port, edge.target_node_id, edge.target_port)
            if key in seen_edges:
                issue(path, "duplicate_edge", "Duplicate connection")
            seen_edges.add(key)
            if source.kind in ("end", "fail") or target.kind == "start":
                issue(
                    path,
                    "invalid_direction",
                    "Connections cannot enter starts or leave terminal nodes",
                )
            adjacency[source.node_id].add(target.node_id)
            reverse[target.node_id].add(source.node_id)

    starts: set[str] = set()
    start_ids: set[str] = set()
    for index, start in enumerate(workflow.start_definitions):
        if start.start_id in start_ids:
            issue(f"/start_definitions/{index}/start_id", "duplicate_id", "Duplicate start ID")
        start_ids.add(start.start_id)
        if start.node_id not in nodes or nodes[start.node_id].kind != "start":
            issue(
                f"/start_definitions/{index}/node_id",
                "invalid_start",
                "Start must reference a start node",
            )
        else:
            starts.add(start.node_id)
    for index, node in enumerate(workflow.nodes):
        if node.kind == "start" and node.node_id not in starts:
            issue(f"/nodes/{index}", "undeclared_start", "Start node requires a start definition")

    def reachable(seeds: set[str], graph: dict[str, set[str]]) -> set[str]:
        visited = set(seeds)
        pending = list(seeds)
        while pending:
            for next_id in graph[pending.pop()]:
                if next_id not in visited:
                    visited.add(next_id)
                    pending.append(next_id)
        return visited

    reached = reachable(starts, adjacency)
    terminals = {node_id for node_id, node in nodes.items() if node.kind in ("end", "fail")}
    if not terminals:
        issue("/nodes", "missing_terminal", "Workflow requires a terminal node")
    can_finish = reachable(terminals, reverse)
    for index, node in enumerate(workflow.nodes):
        if node.node_id not in reached:
            issue(
                f"/nodes/{index}", "unreachable_node", "Node is unreachable from a declared start"
            )
        if node.node_id not in can_finish:
            issue(f"/nodes/{index}", "no_terminal_path", "Node cannot reach a terminal")

    # Explicit loop nodes describe bounded iteration; graph back-edges remain invalid.
    indegree = {node_id: len(parents) for node_id, parents in reverse.items()}
    pending = [node_id for node_id, count in indegree.items() if count == 0]
    processed = 0
    while pending:
        node_id = pending.pop()
        processed += 1
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                pending.append(target_id)
    if processed != len(nodes):
        issue("/edges", "unsupported_cycle", "Use bounded loop nodes instead of cyclic graph edges")
    return issues
