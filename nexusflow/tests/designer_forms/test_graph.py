"""Structured execution checks do not need a catalog, Temporal, or a database."""

import json

from nexusflow.modules.designer_forms.graph import validate_graph
from nexusflow.modules.project_versioning.types import ProjectContent


def project(
    kinds: dict[str, str], edges: list[tuple[str, str]], **changes: object
) -> ProjectContent:
    outgoing: dict[str, list[str]] = {key: [] for key in kinds}
    for source, target in edges:
        outgoing[source].append(target)
    workflow = {
        "schema_version": "0.1",
        "workflow_id": "one",
        "input_schema": {},
        "output_schema": {},
        "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
        "nodes": [
            {
                "node_id": key,
                "kind": kind,
                "named_ports": {"inputs": ["in"], "outputs": outgoing[key]},
                "input_mapping": {},
            }
            for key, kind in kinds.items()
        ],
        "edges": [
            {
                "source_node_id": source,
                "source_port": target,
                "target_node_id": target,
                "target_port": "in",
            }
            for source, target in edges
        ],
        "error_routes": [],
        **changes,
    }
    return ProjectContent.model_validate_json(
        json.dumps({"schema_version": "0.1", "workflows": [workflow]})
    )


def diamond() -> ProjectContent:
    return project(
        {
            "start": "start",
            "split": "parallel",
            "a": "wait",
            "b": "wait",
            "join": "join",
            "end": "end",
        },
        [
            ("start", "split"),
            ("split", "a"),
            ("split", "b"),
            ("a", "join"),
            ("b", "join"),
            ("join", "end"),
        ],
    )


def test_valid_parallel_diamond() -> None:
    assert validate_graph(diamond()) == ()


def test_valid_nested_parallel() -> None:
    content = project(
        {
            "start": "start",
            "p": "parallel",
            "a": "wait",
            "q": "parallel",
            "b": "wait",
            "c": "wait",
            "j": "join",
            "k": "join",
            "end": "end",
        },
        [
            ("start", "p"),
            ("p", "a"),
            ("p", "q"),
            ("q", "b"),
            ("q", "c"),
            ("b", "j"),
            ("c", "j"),
            ("j", "k"),
            ("a", "k"),
            ("k", "end"),
        ],
    )
    assert validate_graph(content) == ()


def test_parallel_path_cannot_bypass_join() -> None:
    content = project(
        {
            "start": "start",
            "p": "parallel",
            "a": "wait",
            "b": "decision",
            "join": "join",
            "end": "end",
        },
        [
            ("start", "p"),
            ("p", "a"),
            ("p", "b"),
            ("a", "join"),
            ("b", "join"),
            ("b", "end"),
            ("join", "end"),
        ],
    )
    assert {item.code for item in validate_graph(content)} == {
        "unmatched_parallel",
        "unmatched_join",
    }


def test_branches_cannot_merge_before_join() -> None:
    content = project(
        {
            "start": "start",
            "p": "parallel",
            "a": "wait",
            "b": "wait",
            "merge": "wait",
            "join": "join",
            "end": "end",
        },
        [
            ("start", "p"),
            ("p", "a"),
            ("p", "b"),
            ("a", "merge"),
            ("b", "merge"),
            ("merge", "join"),
            ("join", "end"),
        ],
    )
    assert "unstructured_parallel" in {item.code for item in validate_graph(content)}


def test_join_cannot_join_conditional_decision_branches() -> None:
    payload = json.loads(diamond().model_dump_json())
    payload["workflows"][0]["nodes"][1]["kind"] = "decision"
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert [(item.path, item.code) for item in issues] == [
        ("/workflows/0/nodes/4", "unmatched_join")
    ]


def test_ordinary_step_cannot_fan_out_even_across_different_ports() -> None:
    content = project(
        {"start": "start", "a": "wait", "b": "wait", "end": "end"},
        [("start", "a"), ("start", "b"), ("a", "end"), ("b", "end")],
    )
    assert validate_graph(content)[0].code == "implicit_split"


def test_decision_requires_connected_distinct_branch_ports() -> None:
    content = project(
        {"start": "start", "d": "decision", "end": "end"}, [("start", "d"), ("d", "end")]
    )
    assert validate_graph(content)[0].code == "invalid_split"


def test_error_route_does_not_make_a_parallel_branch_complete() -> None:
    payload = json.loads(diamond().model_dump_json())
    workflow = payload["workflows"][0]
    workflow["error_routes"] = [workflow["edges"].pop(4)]
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert "unmatched_parallel" in {item.code for item in issues}


def test_ambiguous_parallel_error_recovery_is_located() -> None:
    payload = json.loads(diamond().model_dump_json())
    payload["workflows"][0]["error_routes"] = [
        {
            "source_node_id": "a",
            "source_port": "join",
            "target_node_id": "join",
            "target_port": "in",
        }
    ]
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert [(item.path, item.code) for item in issues] == [
        ("/workflows/0/error_routes/0", "ambiguous_parallel_error")
    ]


def test_child_workflow_reference_is_required_and_recursion_located() -> None:
    content = project(
        {"start": "start", "child": "child_workflow", "end": "end"},
        [("start", "child"), ("child", "end")],
    )
    assert validate_graph(content)[0].code == "missing_child_workflow"
    payload = json.loads(content.model_dump_json())
    payload["workflows"][0]["nodes"][1]["input_mapping"] = {"workflow_id": "one"}
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert [(item.path, item.code) for item in issues] == [
        ("/workflows/0/nodes/1/input_mapping/workflow_id", "recursive_workflow")
    ]


def test_indirect_recursion_reports_every_recursive_call_without_recursion_error() -> None:
    content = project(
        {"start": "start", "child": "child_workflow", "end": "end"},
        [("start", "child"), ("child", "end")],
    )
    payload = json.loads(content.model_dump_json())
    first = payload["workflows"][0]
    first["nodes"][1]["input_mapping"] = {"workflow_id": "two"}
    second = json.loads(json.dumps(first))
    second["workflow_id"] = "two"
    second["nodes"][1]["input_mapping"] = {"workflow_id": "one"}
    payload["workflows"].append(second)
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert len(issues) == 2
    assert {item.code for item in issues} == {"recursive_workflow"}


def test_cycle_is_left_to_foundation_validator_without_hanging() -> None:
    content = project(
        {"start": "start", "a": "wait", "end": "end"}, [("start", "a"), ("a", "start")]
    )
    assert validate_graph(content) == ()


def test_join_cannot_accept_an_unrelated_start_path() -> None:
    payload = json.loads(diamond().model_dump_json())
    workflow = payload["workflows"][0]
    workflow["nodes"].append(
        {
            "node_id": "other",
            "kind": "start",
            "named_ports": {"inputs": [], "outputs": ["out"]},
            "input_mapping": {},
        }
    )
    workflow["start_definitions"].append({"start_id": "other", "node_id": "other", "kind": "api"})
    workflow["edges"].append(
        {
            "source_node_id": "other",
            "source_port": "out",
            "target_node_id": "join",
            "target_port": "in",
        }
    )
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert "unstructured_parallel" in {item.code for item in issues}


def test_one_decision_port_cannot_fan_out_twice() -> None:
    payload = json.loads(diamond().model_dump_json())
    workflow = payload["workflows"][0]
    workflow["nodes"][1]["kind"] = "decision"
    workflow["edges"][2]["source_port"] = "a"
    issues = validate_graph(ProjectContent.model_validate_json(json.dumps(payload)))
    assert "invalid_split" in {item.code for item in issues}


def test_acyclic_child_workflow_chain_is_valid() -> None:
    content = project(
        {"start": "start", "child": "child_workflow", "end": "end"},
        [("start", "child"), ("child", "end")],
    )
    payload = json.loads(content.model_dump_json())
    first = payload["workflows"][0]
    first["nodes"][1]["input_mapping"] = {"workflow_id": "two"}
    second = json.loads(json.dumps(first))
    second["workflow_id"] = "two"
    second["nodes"][1]["kind"] = "wait"
    second["nodes"][1]["input_mapping"] = {}
    payload["workflows"].append(second)
    assert validate_graph(ProjectContent.model_validate_json(json.dumps(payload))) == ()
