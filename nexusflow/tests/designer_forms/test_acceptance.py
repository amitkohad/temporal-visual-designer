"""Independent publication acceptance checks for authoring validation."""

import json
from pathlib import Path

import pytest

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, Scope
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ResolvedArtifact, VersioningError

SCOPE = Scope(organization_id="acme", environment_id="dev")
REF = ActivityRef(
    activity_id="create-account", version="1.0.0", artifact_digest="sha256:" + "a" * 64
)


class Authorization:
    def authorize(self, scope: Scope, permission: str, project_id: str) -> str:
        return "business-author"


class Catalog:
    def __init__(self) -> None:
        self.outage = False
        self.missing = False
        self.mismatched = False

    def resolve(self, reference: ActivityRef) -> ResolvedArtifact:
        return ResolvedArtifact(reference)

    def descriptor(self, reference: ActivityRef) -> ActivityDescriptor | None:
        if self.outage:
            raise OSError("Internal catalog host must not appear in errors")
        if self.missing:
            return None
        return ActivityDescriptor(
            descriptor_schema_version="0.1",
            activity_ref=(
                reference.model_copy(update={"version": "2.0.0"}) if self.mismatched else reference
            ),
            display_name="Create account",
            category="directory",
            description="Create an account from a required employee name",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            },
            configuration_schema={},
            connection_requirements=(),
            execution_policy={"timeout_seconds": 30, "maximum_attempts": 1},
            runtime_requirements=(),
            documentation="",
        )


def node(identifier: str, kind: str, mapping: dict | None = None) -> dict:
    result = {
        "node_id": identifier,
        "kind": kind,
        "named_ports": {
            "inputs": [] if kind == "start" else ["in"],
            "outputs": [] if kind == "end" else ["out"],
        },
        "input_mapping": mapping or {},
    }
    if kind == "activity":
        result["activity_ref"] = REF.model_dump(mode="json")
    return result


def edge(source: str, target: str, port: str = "out") -> dict:
    return {
        "source_node_id": source,
        "source_port": port,
        "target_node_id": target,
        "target_port": "in",
    }


def binding(source: str, path: str, node_id: str | None = None) -> dict:
    value = {"schema_version": "0.1", "source": source, "path": path}
    if node_id is not None:
        value["node_id"] = node_id
    return {"$binding": value}


def project(mapping: dict | None = None) -> dict:
    return {
        "schema_version": "0.1",
        "activity_refs": [REF.model_dump(mode="json")],
        "workflows": [
            {
                "schema_version": "0.1",
                "workflow_id": "onboarding",
                "input_schema": {
                    "type": "object",
                    "properties": {"employee": {"type": "string"}},
                    "required": ["employee"],
                },
                "output_schema": {"type": "object"},
                "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
                "nodes": [
                    node("start", "start"),
                    node("create", "activity", mapping or {"name": binding("input", "/employee")}),
                    node("end", "end"),
                ],
                "edges": [edge("start", "create"), edge("create", "end")],
                "error_routes": [],
            }
        ],
    }


@pytest.fixture
def system(tmp_path: Path):
    repository = SQLiteProjectRepository(tmp_path / "authoring.db")
    catalog = Catalog()
    return (
        ProjectService(repository, Authorization(), AuthoringValidator(catalog), catalog),
        repository,
        catalog,
    )


def publish(system, payload: dict):
    service, _, _ = system
    revision = service.create(SCOPE, "onboarding", "Employee onboarding", json.dumps(payload))
    return service.publish(SCOPE, "onboarding", revision.number, "release-1")


def assert_blocked(system, payload: dict, path_fragment: str) -> None:
    with pytest.raises(VersioningError) as caught:
        publish(system, payload)
    assert caught.value.code == "validation_failed"
    assert any(path_fragment in issue.path for issue in caught.value.issues)
    assert system[1].published(SCOPE, "onboarding", "release-1") is None


def test_typed_workflow_input_binding_publishes_immutable_release(system):
    release = publish(system, project())
    assert release.source_revision == 1
    assert system[1].release(SCOPE, "onboarding", release.release_id) == release
    assert json.loads(release.artifacts_json) == [REF.model_dump(mode="json")]


def test_literal_wrong_type_stays_draft_and_cannot_publish(system):
    assert_blocked(system, project({"name": 42}), "/input_mapping/name")
    assert system[1].revision(SCOPE, "onboarding").number == 1


def test_optional_input_property_cannot_satisfy_required_activity_input(system):
    payload = project()
    payload["workflows"][0]["input_schema"]["required"] = []
    assert_blocked(system, payload, "/input_mapping/name")


def test_step_output_requires_producer_on_every_path(system):
    payload = project()
    workflow = payload["workflows"][0]
    decision = node("route", "decision")
    decision["named_ports"]["outputs"] = ["approved", "rejected"]
    workflow["nodes"] = [
        node("start", "start"),
        decision,
        node("producer", "activity", {"name": "Alice"}),
        node("consumer", "activity", {"name": binding("step", "/account", "producer")}),
        node("end", "end"),
    ]
    workflow["edges"] = [
        edge("start", "route"),
        edge("route", "producer", "approved"),
        edge("producer", "consumer"),
        edge("route", "consumer", "rejected"),
        edge("consumer", "end"),
    ]
    assert_blocked(system, payload, "/input_mapping/name")


@pytest.mark.parametrize("state", ["missing", "mismatched"])
def test_missing_or_wrong_pinned_descriptor_blocks_release(system, state):
    setattr(system[2], state, True)
    assert_blocked(system, project(), "/activity_ref")


def test_catalog_outage_leaves_no_release_and_hides_internal_details(system):
    system[2].outage = True
    with pytest.raises(VersioningError) as caught:
        publish(system, project())
    assert caught.value.code in {"unavailable", "validation_failed"}
    assert "Internal catalog host" not in str(caught.value)
    assert system[1].published(SCOPE, "onboarding", "release-1") is None


def test_authoring_validator_retains_baseline_graph_validation(system):
    payload = project()
    payload["workflows"][0]["edges"] = [edge("start", "create")]
    assert_blocked(system, payload, "/workflows/0")


def test_dominating_activity_output_binding_is_publishable(system):
    payload = project()
    workflow = payload["workflows"][0]
    workflow["nodes"].insert(
        2, node("consumer", "activity", {"name": binding("step", "/account", "create")})
    )
    workflow["edges"] = [
        edge("start", "create"),
        edge("create", "consumer"),
        edge("consumer", "end"),
    ]
    assert publish(system, payload).source_revision == 1


def test_recursive_child_workflow_cannot_publish(system):
    payload = project()
    payload["workflows"][0]["nodes"][1] = node(
        "create", "child_workflow", {"workflow_id": "onboarding"}
    )
    assert_blocked(system, payload, "/workflows/0")


def test_nested_reserved_binding_is_not_executable_mapping(system):
    assert_blocked(
        system, project({"name": {"nested": binding("input", "/employee")}}), "/input_mapping/name"
    )
