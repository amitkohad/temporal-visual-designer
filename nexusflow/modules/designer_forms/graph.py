"""Additional, offline authoring graph checks over the v0.1 graph contract."""

from nexusflow.contracts.models import WorkflowDefinition
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.project_versioning.types import ProjectContent


def _workflow(workflow: WorkflowDefinition, prefix: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    nodes = {node.node_id: node for node in workflow.nodes}
    indexes = {node.node_id: index for index, node in enumerate(workflow.nodes)}
    adjacency: dict[str, set[str]] = {key: set() for key in nodes}
    reverse: dict[str, set[str]] = {key: set() for key in nodes}
    ports: dict[tuple[str, str], int] = {}
    for edge in workflow.edges:
        if edge.source_node_id not in nodes or edge.target_node_id not in nodes:
            continue
        adjacency[edge.source_node_id].add(edge.target_node_id)
        reverse[edge.target_node_id].add(edge.source_node_id)
        port_key = (edge.source_node_id, edge.source_port)
        ports[port_key] = ports.get(port_key, 0) + 1

    def issue(node_id: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(f"{prefix}/nodes/{indexes[node_id]}", code, message))

    for node in workflow.nodes:
        if node.kind in ("decision", "parallel"):
            if len(node.named_ports.outputs) < 2 or any(
                ports.get((node.node_id, port), 0) != 1 for port in node.named_ports.outputs
            ):
                issue(
                    node.node_id,
                    "invalid_split",
                    "A split needs at least two connected, distinct output ports",
                )
        elif sum(count for (source, _), count in ports.items() if source == node.node_id) > 1:
            issue(
                node.node_id,
                "implicit_split",
                "Use a decision or parallel block to split normal execution",
            )

    # Reverse topological processing computes postdominators without recursion.
    degree = {key: len(targets) for key, targets in adjacency.items()}
    pending = [key for key in nodes if degree[key] == 0]
    post: dict[str, set[str]] = {}
    while pending:
        key = pending.pop()
        targets = adjacency[key]
        common = set.intersection(*(post[target] for target in targets)) if targets else set()
        post[key] = common | {key}
        for parent in reverse[key]:
            degree[parent] -= 1
            if degree[parent] == 0:
                pending.append(parent)
    # Foundation validator reports graph cycles and malformed references.
    if len(post) != len(nodes):
        return issues

    matched: dict[str, str] = {}
    for node in workflow.nodes:
        if node.kind != "parallel":
            continue
        starts = adjacency[node.node_id]
        if len(starts) < 2:
            issue(node.node_id, "invalid_parallel", "Parallel branches must enter distinct nodes")
            continue
        common = set.intersection(*(post[start] for start in starts))
        candidates = {key for key in common if nodes[key].kind == "join"}
        nearest = [
            key for key in candidates if not any(key in post[other] for other in candidates - {key})
        ]
        if len(nearest) != 1:
            issue(
                node.node_id,
                "unmatched_parallel",
                "Every parallel path must reach one structured join",
            )
            continue
        join = nearest[0]
        regions: list[set[str]] = []
        for start in sorted(starts):
            region: set[str] = set()
            work = [start]
            while work:
                key = work.pop()
                if key == join or key in region:
                    continue
                region.add(key)
                work.extend(adjacency[key] - region)
            regions.append(region)
        overlap = any(
            left & right for index, left in enumerate(regions) for right in regions[index + 1 :]
        )
        region_nodes = set().union(*regions)
        external_entry = any(reverse[key] - region_nodes - {node.node_id} for key in region_nodes)
        unrelated_incoming = bool(reverse[join] - region_nodes - {node.node_id})
        if overlap or external_entry or unrelated_incoming or join in matched:
            issue(
                node.node_id,
                "unstructured_parallel",
                "Parallel branches must remain separate until their own join",
            )
        else:
            matched[join] = node.node_id
        for index, edge in enumerate(workflow.error_routes):
            source_inside = edge.source_node_id in region_nodes
            target_inside = edge.target_node_id in region_nodes or edge.target_node_id == join
            target = nodes.get(edge.target_node_id)
            if (source_inside or target_inside) and not (
                source_inside and target and target.kind == "fail"
            ):
                issues.append(
                    ValidationIssue(
                        f"{prefix}/error_routes/{index}",
                        "ambiguous_parallel_error",
                        "Parallel error recovery must terminate explicitly with a fail block",
                    )
                )

    for node in workflow.nodes:
        if node.kind == "join" and (len(reverse[node.node_id]) < 2 or node.node_id not in matched):
            issue(
                node.node_id,
                "unmatched_join",
                "A join needs distinct incoming branches from one parallel block",
            )
    return issues


def validate_graph(content: ProjectContent) -> tuple[ValidationIssue, ...]:
    """Validate split/join structure and reusable workflow references deterministically."""
    issues: list[ValidationIssue] = []
    workflows = {workflow.workflow_id: workflow for workflow in content.workflows}
    dependencies: dict[str, set[str]] = {key: set() for key in workflows}
    references: list[tuple[str, str, str]] = []
    for wi, workflow in enumerate(content.workflows):
        prefix = f"/workflows/{wi}"
        issues.extend(_workflow(workflow, prefix))
        for ni, node in enumerate(workflow.nodes):
            if node.kind != "child_workflow":
                continue
            target = node.input_mapping.get("workflow_id")
            path = f"{prefix}/nodes/{ni}/input_mapping/workflow_id"
            if not isinstance(target, str) or target not in workflows:
                issues.append(
                    ValidationIssue(
                        path, "missing_child_workflow", "Select a workflow in this project"
                    )
                )
            else:
                dependencies[workflow.workflow_id].add(target)
                references.append((workflow.workflow_id, target, path))
    for source, target, path in references:
        visited: set[str] = set()
        pending = [target]
        while pending:
            key = pending.pop()
            if key == source:
                issues.append(
                    ValidationIssue(
                        path,
                        "recursive_workflow",
                        "Reusable workflow references must not be recursive",
                    )
                )
                break
            if key not in visited:
                visited.add(key)
                pending.extend(sorted(dependencies[key] - visited))
    return tuple(issues)
