"""Signed destination intake never installs or activates the uploaded project."""

import io
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_project_packages import download, publish


@pytest.fixture
def imports(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    archive = download(original, publish(original)).content
    repository = SQLitePackageImportRepository(
        str(tmp_path / "imports.db"), maximum_imports_per_scope=3
    )
    configured = replace(backend, package_imports=repository)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, configured, data, runtime, archive
    repository.close()


def stage(client, archive, operation="first", base=BASE):
    return client.put(
        base + "/package-imports/" + operation,
        content=archive,
        headers={"content-type": "application/zip"},
    )


def test_stage_download_retry_and_no_installation(imports):
    client, backend, data, runtime, archive = imports
    result = stage(client, archive)
    assert result.status_code == 201, result.text
    metadata = result.json()
    assert metadata["state"] == "staged" and metadata["revision"] == 1
    assert metadata["actor_id"]
    assert stage(client, archive).json() == metadata
    path = BASE + "/package-imports/first"
    assert client.get(path).json() == metadata
    response = client.get(path + "/archive")
    assert response.content == archive
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-package-digest"] == metadata["package_digest"]
    assert (
        response.headers["content-disposition"] == 'attachment; filename="nexus-flow-project.zip"'
    )
    manifest = client.get(path + "/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["package_digest"] == metadata["package_digest"]
    assert backend.projects.project(data[1], "hr").head_revision == 1
    assert backend.activations.current(data[1], "hr") is None
    assert not runtime.plans


def test_repacked_zip_retry_keeps_first_archive(imports):
    client, _, _, _, archive = imports
    first = stage(client, archive).json()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(output, "w") as target:
        for name in reversed(source.namelist()):
            target.writestr(name, source.read(name), compress_type=zipfile.ZIP_STORED)
    assert output.getvalue() != archive
    assert stage(client, output.getvalue()).json() == first
    assert client.get(BASE + "/package-imports/first/archive").content == archive


def test_changed_package_conflicts_without_replacing_operation(imports):
    client, _, _, _, archive = imports
    first = stage(client, archive).json()
    revision = client.get(BASE + "/projects/hr").json()
    revision["content"]["layout"] = {"changed": True}
    saved = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "New draft", "content": revision["content"]},
    )
    assert saved.status_code == 200
    release = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 2, "idempotency_key": "release-b"}
    )
    assert release.status_code == 201
    changed = download(client, release.json()["release_id"]).content
    assert stage(client, changed).status_code == 409
    assert client.get(BASE + "/package-imports/first").json() == first
    assert client.get(BASE + "/package-imports/first/archive").content == archive


def test_history_bounds_and_scope_quota(imports):
    client, _, _, _, archive = imports
    for operation in ("one", "two", "three"):
        assert stage(client, archive, operation).status_code == 201
    page = client.get(BASE + "/package-imports?limit=2").json()
    assert [item["revision"] for item in page["items"]] == [3, 2]
    assert page["next_before_revision"] == 2
    tail = client.get(BASE + "/package-imports?before_revision=2").json()
    assert [item["revision"] for item in tail["items"]] == [1]
    assert tail["next_before_revision"] is None
    assert stage(client, archive, "four").status_code == 409
    assert stage(client, archive, "one").status_code == 201
    for query in (
        "limit=0",
        "limit=101",
        "before_revision=0",
        "before_revision=9223372036854775808",
    ):
        assert client.get(BASE + "/package-imports?" + query).status_code == 422


def test_media_validation_corrupt_and_unknown_id(imports):
    client, _, _, _, _ = imports
    path = BASE + "/package-imports/first"
    assert client.put(path, json={"actor_id": "spoof"}).status_code == 415
    assert stage(client, b"not a zip").status_code == 422
    assert client.get(path).status_code == 404
    assert client.get(path + "/manifest").status_code == 404
    assert client.get(path + "/archive").status_code == 404
    assert stage(client, b"bad", "invalid!").status_code == 422


def test_fresh_revocation_and_environment_isolation(imports):
    client, _, data, _, archive = imports
    repository, _, grant, _, _ = data
    assert stage(client, archive).status_code == 201
    assert client.get(BASE.replace("/dev", "/uat") + "/package-imports/first").status_code == 403
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    for suffix in ("", "/first", "/first/manifest", "/first/archive"):
        assert client.get(BASE + "/package-imports" + suffix).status_code == 403
    assert client.put(BASE + "/package-imports/first", json={}).status_code == 403


def test_unconfigured_provider_authorizes_before_disclosing(api):  # noqa: F811
    client, _, data, _ = api
    assert client.get(BASE + "/package-imports").status_code == 503
    assert client.put(BASE + "/package-imports/first", json={}).status_code == 503
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(BASE + "/package-imports").status_code == 403
    assert client.put(BASE + "/package-imports/first", json={}).status_code == 403


def test_openapi_has_typed_metadata_and_binary_transfer(imports):
    _, backend, _, _, _ = imports
    spec = create_app(backend).openapi()
    prefix = "/api/v1/organizations/{organization_id}/environments/{environment_id}"
    upload = spec["paths"][prefix + "/package-imports/{operation_id}"]["put"]
    assert upload["requestBody"]["content"]["application/zip"]["schema"]["format"] == "binary"
    assert upload["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "PackageImportMetadata"
    )
    download = spec["paths"][prefix + "/package-imports/{operation_id}/archive"]["get"]
    assert (
        download["responses"]["200"]["content"]["application/zip"]["schema"]["format"] == "binary"
    )


def test_uat_and_prod_intake_require_server_owned_grants(imports):
    client, backend, data, runtime, archive = imports
    repository, _, grant, _, token = data
    uat = Scope(organization_id="acme", environment_id="uat")
    prod = Scope(organization_id="acme", environment_id="prod")
    repository.save_grant(replace(grant, scope=uat), expected_revision=0)
    repository.save_grant(replace(grant, scope=prod), expected_revision=0)
    uat_base, prod_base = BASE.replace("/dev", "/uat"), BASE.replace("/dev", "/prod")
    assert stage(client, archive, base=uat_base).status_code == 201
    assert stage(client, archive, base=prod_base).status_code == 403
    client.headers["authorization"] = "Bearer " + token(roles=["production_support"])
    assert stage(client, archive, base=prod_base).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod, role="production_support"), expected_revision=1
    )
    assert stage(client, archive, base=prod_base).status_code == 201
    assert client.get(prod_base + "/package-imports/first/manifest").status_code == 200
    assert backend.projects.list_projects(uat) == ()
    assert backend.projects.list_projects(prod) == ()
    assert backend.activations.current(prod, "hr") is None
    assert not runtime.plans
