"""Independent acceptance scenarios for immutable project revisions and releases."""

import json
from pathlib import Path

import pytest

from nexusflow.contracts.models import ActivityRef, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import (
    ProjectContent,
    ResolvedArtifact,
    VersioningError,
)

SCOPE = Scope(organization_id="acme", environment_id="dev")
FOREIGN = Scope(organization_id="other", environment_id="dev")


class Authorization:
    def __init__(self) -> None:
        self.calls: list[tuple[Scope, str, str]] = []
        self.denied = False

    def authorize(self, scope: Scope, permission: str, project_id: str) -> str:
        self.calls.append((scope, permission, project_id))
        if self.denied:
            raise VersioningError("forbidden", "Access denied")
        return "business-author"


class Validator:
    def __init__(self) -> None:
        self.issues: tuple[ValidationIssue, ...] = ()

    def validate(self, content: ProjectContent) -> tuple[ValidationIssue, ...]:
        return self.issues


class Catalog:
    def __init__(self) -> None:
        self.artifacts: dict[str, ResolvedArtifact] = {}

    def resolve(self, reference: ActivityRef) -> ResolvedArtifact | None:
        return self.artifacts.get(reference.activity_id)


def content(*, position: int = 0, refs: tuple[ActivityRef, ...] = ()) -> str:
    return json.dumps(
        {
            "schema_version": "0.1",
            "workflows": [
                {
                    "schema_version": "0.1",
                    "workflow_id": "onboarding",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                    "start_definitions": [
                        {"start_id": "manual", "node_id": "start", "kind": "manual"}
                    ],
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
            ],
            "activity_refs": [ref.model_dump(mode="json") for ref in refs],
            "layout": {"start": {"x": position}},
        }
    )


def reference(name: str, digit: str = "a") -> ActivityRef:
    return ActivityRef(activity_id=name, version="1.0.0", artifact_digest="sha256:" + digit * 64)


@pytest.fixture
def system(tmp_path: Path):
    repository = SQLiteProjectRepository(tmp_path / "projects.db")
    auth, validator, catalog = Authorization(), Validator(), Catalog()
    return (
        ProjectService(repository, auth, validator, catalog),
        repository,
        auth,
        validator,
        catalog,
    )


def test_stale_writer_cannot_replace_current_revision(system):
    service, _, _, _, _ = system
    first = service.create(SCOPE, "hr", "HR", content())
    second = service.save(SCOPE, "hr", first.number, content(position=10), "Move start")
    with pytest.raises(VersioningError) as error:
        service.save(SCOPE, "hr", first.number, content(position=20), "Stale edit")
    assert error.value.code == "conflict"
    assert service.read(SCOPE, "hr") == second
    assert service.read(SCOPE, "hr", first.number).content_json == first.content_json


def test_restore_appends_revision_preserving_original_history(system):
    service, _, _, _, _ = system
    first = service.create(SCOPE, "hr", "HR", content())
    second = service.save(SCOPE, "hr", first.number, content(position=10), "Move")
    restored = service.restore(SCOPE, "hr", first.number, second.number)
    assert restored.number > second.number
    assert restored.parent_revision == second.number
    assert restored.content_json == first.content_json
    assert {revision.number for revision in service.history(SCOPE, "hr")} == {
        first.number,
        second.number,
        restored.number,
    }


def test_retry_returns_original_release_even_when_validation_later_changes(system):
    service, _, _, validator, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    release = service.publish(SCOPE, "hr", revision.number, "release-1")
    validator.issues = (ValidationIssue("/workflows", "invalid_input", "Blocked"),)
    assert service.publish(SCOPE, "hr", revision.number, "release-1") == release
    assert service.get_release(SCOPE, "hr", release.release_id) == release


def test_idempotency_key_cannot_publish_different_revision(system):
    service, _, _, _, _ = system
    first = service.create(SCOPE, "hr", "HR", content())
    service.publish(SCOPE, "hr", first.number, "release-1")
    second = service.save(SCOPE, "hr", first.number, content(position=10), "Move")
    with pytest.raises(VersioningError) as error:
        service.publish(SCOPE, "hr", second.number, "release-1")
    assert error.value.code == "conflict"


def test_missing_transitive_artifact_prevents_publication(system):
    service, repository, _, _, catalog = system
    direct, missing = reference("notify"), reference("http", "b")
    catalog.artifacts[direct.activity_id] = ResolvedArtifact(direct, (missing,))
    revision = service.create(SCOPE, "hr", "HR", content(refs=(direct,)))
    with pytest.raises(VersioningError):
        service.publish(SCOPE, "hr", revision.number, "missing")
    assert repository.published(SCOPE, "hr", "missing") is None


def test_layout_changes_full_digest_without_changing_executable_digest(system):
    service, _, _, _, _ = system
    first = service.create(SCOPE, "hr", "HR", content())
    release1 = service.publish(SCOPE, "hr", first.number, "first")
    second = service.save(SCOPE, "hr", first.number, content(position=99), "Move")
    release2 = service.publish(SCOPE, "hr", second.number, "second")
    assert release1.content_digest != release2.content_digest
    assert release1.executable_digest == release2.executable_digest
    assert service.compare(SCOPE, "hr", first.number, second.number) == ()
    assert service.compare(SCOPE, "hr", first.number, second.number, include_layout=True)
    assert service.get_release(SCOPE, "hr", release1.release_id).content_json == first.content_json


def test_authorization_is_rechecked_after_access_revocation(system):
    service, _, auth, _, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    service.read(SCOPE, "hr")
    assert len(auth.calls) == 2
    auth.denied = True
    for operation in (
        lambda: service.read(SCOPE, "hr"),
        lambda: service.history(SCOPE, "hr"),
        lambda: service.publish(SCOPE, "hr", revision.number, "blocked"),
    ):
        with pytest.raises(VersioningError) as error:
            operation()
        assert error.value.code == "forbidden"
    assert len(auth.calls) == 5


def test_scope_cannot_retrieve_another_organizations_revision_or_release(system):
    service, _, _, _, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    release = service.publish(SCOPE, "hr", revision.number, "release")
    for operation in (
        lambda: service.read(FOREIGN, "hr"),
        lambda: service.get_release(FOREIGN, "hr", release.release_id),
    ):
        with pytest.raises(VersioningError) as error:
            operation()
        assert error.value.code == "not_found"


def test_retained_release_cannot_be_deleted_until_reference_removed(system):
    service, repository, _, _, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    release = service.publish(SCOPE, "hr", revision.number, "release")
    repository.retain(SCOPE, "hr", release.release_id, "execution-123")
    with pytest.raises(VersioningError):
        service.delete_release(SCOPE, "hr", release.release_id)
    assert service.get_release(SCOPE, "hr", release.release_id) == release
    repository.release_reference(SCOPE, "hr", release.release_id, "execution-123")
    service.delete_release(SCOPE, "hr", release.release_id)
    with pytest.raises(VersioningError) as error:
        service.get_release(SCOPE, "hr", release.release_id)
    assert error.value.code == "not_found"


def test_release_contains_whole_project_and_exact_transitive_artifact_closure(system):
    service, _, _, _, catalog = system
    direct, dependency = reference("notify"), reference("http", "b")
    catalog.artifacts[direct.activity_id] = ResolvedArtifact(direct, (dependency,))
    catalog.artifacts[dependency.activity_id] = ResolvedArtifact(dependency)
    payload = json.loads(content(refs=(direct,)))
    purchasing = json.loads(content())["workflows"][0]
    purchasing["workflow_id"] = "purchasing"
    payload["workflows"].append(purchasing)
    payload["forms"] = [
        {"schema_version": "0.1", "form_id": "request", "data_schema": {"type": "object"}}
    ]
    revision = service.create(SCOPE, "business", "Business", json.dumps(payload))
    release = service.publish(SCOPE, "business", revision.number, "release")
    frozen = json.loads(release.content_json)
    assert {workflow["workflow_id"] for workflow in frozen["workflows"]} == {
        "onboarding",
        "purchasing",
    }
    assert frozen["forms"][0]["form_id"] == "request"
    assert json.loads(release.artifacts_json) == [
        dependency.model_dump(mode="json"),
        direct.model_dump(mode="json"),
    ]
    catalog.artifacts.clear()
    assert service.publish(SCOPE, "business", revision.number, "release") == release


def test_validation_failure_preserves_located_issue_without_creating_release(system):
    service, repository, _, validator, _ = system
    revision = service.create(SCOPE, "hr", "HR", content())
    issue = ValidationIssue("/workflows/0/nodes/1", "invalid_input", "Invalid node")
    validator.issues = (issue,)
    with pytest.raises(VersioningError) as error:
        service.publish(SCOPE, "hr", revision.number, "invalid")
    assert error.value.issues == (issue,)
    assert repository.published(SCOPE, "hr", "invalid") is None


def test_uat_authoring_is_rejected_after_authorization(system):
    service, _, auth, _, _ = system
    uat = Scope(organization_id="acme", environment_id="uat")
    with pytest.raises(VersioningError) as error:
        service.create(uat, "hr", "HR", content())
    assert error.value.code == "forbidden"
    assert auth.calls == [(uat, "project.write", "hr")]
