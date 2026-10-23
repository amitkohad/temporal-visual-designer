"""Publication checks are independently testable without catalog or database."""

import json

from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator


def content(**changes: object) -> ProjectContent:
    workflow = {
        "schema_version": "0.1",
        "workflow_id": "onboarding",
        "input_schema": {},
        "output_schema": {},
        "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
        "nodes": [
            {
                "node_id": "start",
                "kind": "start",
                "named_ports": {"inputs": [], "outputs": ["out"]},
                "input_mapping": {},
            },
            {
                "node_id": "end",
                "kind": "end",
                "named_ports": {"inputs": ["in"], "outputs": []},
                "input_mapping": {},
            },
        ],
        "edges": [
            {
                "source_node_id": "start",
                "source_port": "out",
                "target_node_id": "end",
                "target_port": "in",
            }
        ],
        "error_routes": [],
    }
    payload = {"schema_version": "0.1", "workflows": [workflow], **changes}
    return ProjectContent.model_validate_json(json.dumps(payload))


def test_complete_project_and_schema_credentials_are_allowed() -> None:
    project = content(
        forms=[
            {
                "form_id": "employee",
                "schema_version": "0.1",
                "data_schema": {"type": "object", "properties": {"password": {"type": "string"}}},
            }
        ],
        configuration_schema={"properties": {"api_key": {"type": "string"}}},
    )
    assert BuiltinContentValidator().validate(project) == ()


def test_empty_project_cannot_be_published() -> None:
    assert BuiltinContentValidator().validate(content(workflows=[]))[0].code == "missing_workflow"


def test_graph_errors_have_project_locations() -> None:
    payload = json.loads(content().model_dump_json())
    payload["workflows"][0]["edges"] = []
    issues = BuiltinContentValidator().validate(
        ProjectContent.model_validate_json(json.dumps(payload))
    )
    assert any(
        issue.path == "/workflows/0/nodes/1" and issue.code == "unreachable_node"
        for issue in issues
    )


def test_missing_form_connection_and_child_workflow_are_located() -> None:
    payload = json.loads(content().model_dump_json())
    payload["workflows"][0]["nodes"][0]["input_mapping"] = {
        "form_id": "absent",
        "connection_id": "absent",
        "workflow_id": "absent",
    }
    issues = BuiltinContentValidator().validate(
        ProjectContent.model_validate_json(json.dumps(payload))
    )
    assert {issue.path for issue in issues} == {
        f"/workflows/0/nodes/0/input_mapping/{key}"
        for key in ("form_id", "connection_id", "workflow_id")
    }


def test_secret_values_are_located_but_never_echoed() -> None:
    project = content(
        layout={"a/b": [{"client_secret": "sensitive-value"}]},
        tests=[
            {
                "scenario_id": "one",
                "workflow_id": "onboarding",
                "input": {"nested": {"base_url": "https://private.example"}},
            }
        ],
    )
    issues = BuiltinContentValidator().validate(project)
    assert {issue.path for issue in issues} == {
        "/layout/a~1b/0/client_secret",
        "/tests/0/input/nested/base_url",
    }
    assert "sensitive-value" not in repr(issues)
    assert "private.example" not in repr(issues)


def test_duplicate_forms_and_missing_test_workflow() -> None:
    form = {"form_id": "same", "schema_version": "0.1", "data_schema": {}}
    issues = BuiltinContentValidator().validate(
        content(
            forms=[form, form],
            tests=[{"scenario_id": "test", "workflow_id": "missing", "input": {}}],
        )
    )
    assert {(issue.path, issue.code) for issue in issues} == {
        ("/forms/1/form_id", "duplicate_id"),
        ("/tests/0/workflow_id", "missing_resource"),
    }


def test_activity_must_be_declared_and_version_digest_unambiguous() -> None:
    ref = {"activity_id": "notify", "version": "1.0.0", "artifact_digest": "sha256:" + "a" * 64}
    other = {**ref, "artifact_digest": "sha256:" + "b" * 64}
    payload = json.loads(content(activity_refs=[ref, ref, other]).model_dump_json())
    payload["workflows"][0]["nodes"][0]["activity_ref"] = {**ref, "activity_id": "unknown"}
    issues = BuiltinContentValidator().validate(
        ProjectContent.model_validate_json(json.dumps(payload))
    )
    assert {issue.code for issue in issues} == {
        "duplicate_id",
        "conflicting_activity",
        "undeclared_activity",
    }
