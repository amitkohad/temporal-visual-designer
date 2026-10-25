"""Signed package transfer boundaries and bounded provider orchestration."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts.models import ActivityRef, Scope
from nexusflow.modules.activity_catalog.catalog_types import ExportedActivity
from nexusflow.modules.package_promotion.package_format import PackageLimits, decode_package
from nexusflow.modules.package_promotion.packages import ProjectPackageService
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.types import VersioningError
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.api.test_api import BASE, api, create, identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content


def publish(client):
    assert create(client).status_code == 201
    result = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 1, "idempotency_key": "r1"}
    )
    assert result.status_code == 201, result.text
    return result.json()["release_id"]


def download(client, release):
    return client.get(BASE + "/projects/hr/releases/" + release + "/package")


def inspect(client, data, base=BASE):
    return client.post(
        base + "/packages/validation", content=data, headers={"content-type": "application/zip"}
    )


def test_signed_export_roundtrip_is_immutable_and_inspection_has_no_mutations(api):  # noqa: F811
    client, backend, data, runtime = api
    release = publish(client)
    scope = data[1]
    before = backend.projects.revision(scope, "hr", 1)
    response = download(client, release)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="nexus-flow-project.zip"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    package = decode_package(response.content)
    assert response.headers["x-package-digest"] == package.manifest.package_digest
    assert package.content.model_dump(mode="json") == json.loads(before.content_json)
    validated = inspect(client, response.content)
    assert validated.status_code == 200, validated.text
    assert validated.json() == package.manifest.model_dump(mode="json")
    assert "archive" not in validated.json()
    assert validated.json()["activity_refs"] == []
    assert backend.projects.revision(scope, "hr", 1) == before
    assert backend.activations.current(scope, "hr") is None
    assert not runtime.plans
    assert download(client, release).content == response.content


def test_export_uses_published_snapshot_after_draft_changes(api):  # noqa: F811
    client, _, _, _ = api
    release = publish(client)
    original = download(client, release).content
    edited = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "Edit", "content": content("B")},
    )
    assert edited.status_code == 200
    assert download(client, release).content == original


def test_export_includes_exact_published_python_source_and_withdrawal_denies_new_export(api):  # noqa: F811
    client, backend, data, _ = api
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
    archive, digest = source_bundle()
    imported = client.post(
        BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
    )
    assert imported.status_code == 201, imported.text
    reference = imported.json()["manifest"]["descriptor"]["activity_ref"]
    result = client.post(BASE + "/activities/publish", json={"artifact_digest": digest})
    assert result.status_code == 201, result.text
    repository.save_grant(replace(grant, role="business_author"), expected_revision=2)
    project = content()
    project["activity_refs"] = [reference]
    result = client.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project}
    )
    assert result.status_code == 201, result.text
    result = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 1, "idempotency_key": "r1"}
    )
    assert result.status_code == 201, result.text
    release = result.json()["release_id"]
    exported = download(client, release)
    assert exported.status_code == 200, exported.text
    package = decode_package(exported.content)
    assert package.activities[0].archive == archive
    assert package.activities[0].reference == ActivityRef.model_validate(reference)
    # Source limits count raw Python files, not the containing ZIP's metadata overhead.
    source_size = len(b"def run(value):\n    return value\n")
    assert len(archive) > source_size
    catalog = Mock()
    catalog.export.return_value = package.activities[0]
    small_source_budget = ProjectPackageService(
        backend.projects, catalog, Mock(), PackageLimits(maximum_source_bytes=source_size)
    )
    assert small_source_budget.export(data[1], "hr", release).archive == exported.content
    assert inspect(client, exported.content).status_code == 200
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=3)
    transitioned = client.post(
        BASE + "/activities/transitions",
        json={"reference": reference, "target": "withdrawn", "expected_generation": 1},
    )
    assert transitioned.status_code == 200, transitioned.text
    repository.save_grant(replace(grant, role="business_author"), expected_revision=4)
    assert download(client, release).status_code == 422


def test_same_organization_uat_inspection_and_production_support_policy(api):  # noqa: F811
    client, _, data, _ = api
    repository, scope, grant, _, token = data
    package = download(client, publish(client)).content
    uat = Scope(organization_id="acme", environment_id="uat")
    prod = Scope(organization_id="acme", environment_id="prod")
    repository.save_grant(replace(grant, scope=uat), expected_revision=0)
    repository.save_grant(replace(grant, scope=prod), expected_revision=0)
    assert inspect(client, package, BASE.replace("/dev", "/uat")).status_code == 200
    assert inspect(client, package, BASE.replace("/dev", "/prod")).status_code == 403
    client.headers["authorization"] = "Bearer " + token(roles=["production_support"])
    assert inspect(client, package, BASE.replace("/dev", "/prod")).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod, role="production_support"), expected_revision=1
    )
    assert inspect(client, package, BASE.replace("/dev", "/prod")).status_code == 200
    assert download(client, "missing").status_code == 404
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert download(client, "missing").status_code == 403
    assert inspect(client, package).status_code == 403


@pytest.mark.parametrize("media", [None, "application/json", "application/octet-stream"])
def test_media_validation_is_safe(api, media):  # noqa: F811
    client, _, _, _ = api
    headers = {} if media is None else {"content-type": media}
    result = client.post(BASE + "/packages/validation", content=b"private", headers=headers)
    assert result.status_code == 415
    assert "private" not in result.text


@pytest.mark.parametrize("body", [b"", b"private-invalid-zip", b"PK\x03\x04"])
def test_malformed_archive_rejection_is_safe(api, body):  # noqa: F811
    client, _, _, _ = api
    result = inspect(client, body)
    assert result.status_code == 422, result.text
    assert result.json()["code"] == "invalid_input"
    assert "private" not in result.text
    assert result.headers["cache-control"] == "no-store"


def test_global_body_limit_and_openapi_binary_contract(api):  # noqa: F811
    client, backend, _, _ = api
    result = inspect(client, b"x" * 6_000_001)
    assert result.status_code == 413
    paths = create_app(backend).openapi()["paths"]
    prefix = "/api/v1/organizations/{organization_id}/environments/{environment_id}"
    validation = paths[prefix + "/packages/validation"]["post"]
    assert validation["requestBody"]["content"]["application/zip"]["schema"]["format"] == "binary"
    exported = paths[prefix + "/projects/{project_id}/releases/{release_id}/package"]["get"]
    assert "application/zip" in exported["responses"]["200"]["content"]


@pytest.mark.parametrize("failure", [OSError, RuntimeError])
def test_release_storage_errors_have_safe_http_mapping(api, failure):  # noqa: F811
    client, backend, _, _ = api
    projects = Mock(wraps=backend.projects)
    projects.release.side_effect = failure("private-provider-details")
    with TestClient(create_app(replace(backend, projects=projects))) as other:
        other.headers.update(client.headers)
        response = download(other, "r1")
    assert response.status_code == 503
    assert "private-provider-details" not in response.text


def test_service_authorizes_before_lookup_and_rejects_foreign_package(api):  # noqa: F811
    client, backend, data, _ = api
    package = download(client, publish(client)).content
    auth = Mock()
    projects, catalog = Mock(), Mock()
    service = ProjectPackageService(projects, catalog, auth)
    auth.authorize.side_effect = VersioningError("forbidden", "Denied")
    with pytest.raises(VersioningError):
        service.export(data[1], "invalid/id", "missing")
    projects.release.assert_not_called()
    auth.authorize.side_effect = None
    with pytest.raises(ActivationError) as error:
        service.validate(Scope(organization_id="other", environment_id="uat"), package)
    assert error.value.code == "forbidden"
    projects.release.assert_not_called()
    catalog.export.assert_not_called()


@pytest.mark.parametrize("raw", ["{}", "[{}]", "[", '[{"activity_id":"a","activity_id":"b"}]'])
def test_malformed_release_index_fails_before_catalog(api, raw):  # noqa: F811
    client, backend, data, _ = api
    release_id = publish(client)
    release = backend.projects.release(data[1], "hr", release_id)
    projects, catalog, auth = Mock(), Mock(), Mock()
    projects.release.return_value = replace(release, artifacts_json=raw)
    with pytest.raises(ActivationError) as error:
        ProjectPackageService(projects, catalog, auth).export(data[1], "hr", release_id)
    assert error.value.code == "invalid_package"
    catalog.export.assert_not_called()


def test_configured_activity_limit_prevents_any_catalog_fetch(api):  # noqa: F811
    client, backend, data, _ = api
    release_id = publish(client)
    release = backend.projects.release(data[1], "hr", release_id)
    refs = [
        {"activity_id": str(i), "version": "1.0.0", "artifact_digest": "sha256:" + "a" * 64}
        for i in range(2)
    ]
    projects, catalog, auth = Mock(), Mock(), Mock()
    projects.release.return_value = replace(release, artifacts_json=json.dumps(refs))
    with pytest.raises(ActivationError) as error:
        ProjectPackageService(projects, catalog, auth, PackageLimits(maximum_activities=1)).export(
            data[1], "hr", release_id
        )
    assert error.value.code == "package_limit"
    catalog.export.assert_not_called()


def test_configured_archive_budget_stops_before_fetching_next_activity(api):  # noqa: F811
    client, backend, data, _ = api
    release_id = publish(client)
    release = backend.projects.release(data[1], "hr", release_id)
    refs = tuple(
        ActivityRef(activity_id=str(i), version="1.0.0", artifact_digest="sha256:" + "a" * 64)
        for i in range(2)
    )
    projects, catalog, auth = Mock(), Mock(), Mock()
    projects.release.return_value = replace(
        release, artifacts_json=json.dumps([ref.model_dump(mode="json") for ref in refs])
    )
    catalog.export.return_value = ExportedActivity(refs[0], "{}", b"123")
    with pytest.raises(ActivationError) as error:
        limits = PackageLimits(maximum_expanded_bytes=len(release.content_json.encode()) + 2)
        ProjectPackageService(projects, catalog, auth, limits).export(data[1], "hr", release_id)
    assert error.value.code == "package_limit"
    catalog.export.assert_called_once_with(data[1], refs[0])
