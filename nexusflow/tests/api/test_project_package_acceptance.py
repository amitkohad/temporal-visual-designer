"""Independent signed-HTTP acceptance for portable whole-project packages."""

import hashlib
import io
import json
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.identity_environments.types import DirectoryBinding
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.identity.test_service import CLIENT, TENANT, USER
from nexusflow.tests.package_promotion.test_activation import content


def entries(archive):
    with zipfile.ZipFile(io.BytesIO(archive)) as opened:
        return {name: opened.read(name) for name in opened.namelist()}


def repack(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, value in reversed(list(files.items())):
            archive.writestr(name, value)
    return buffer.getvalue()


def validate(client, archive, base=BASE):
    return client.post(
        base + "/packages/validation", content=archive, headers={"content-type": "application/zip"}
    )


def release(client, payload):
    created = client.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": payload}
    )
    assert created.status_code == 201, created.text
    published = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 1, "idempotency_key": "a"}
    )
    assert published.status_code == 201, published.text
    return published.json()["release_id"]


def export(client, release_id):
    response = client.get(BASE + "/projects/hr/releases/" + release_id + "/package")
    assert response.status_code == 200, response.text
    return response


def test_whole_project_and_exact_python_archive_are_portable_without_execution(api):  # noqa: F811
    client, backend, data, _ = api
    repository, scope, grant, _, _ = data
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
    source, digest = source_bundle(b"raise RuntimeError('package-source-must-not-execute')\n")
    imported = client.post(
        BASE + "/activity-imports", content=source, headers={"content-type": "application/zip"}
    )
    assert imported.status_code == 201, imported.text
    reference = imported.json()["manifest"]["descriptor"]["activity_ref"]
    assert (
        client.post(BASE + "/activities/publish", json={"artifact_digest": digest}).status_code
        == 201
    )
    repository.save_grant(replace(grant, role="business_author"), expected_revision=2)
    payload = content()
    payload["activity_refs"] = [reference]
    payload["forms"] = [
        {
            "schema_version": "0.1",
            "form_id": "request",
            "data_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }
    ]
    payload["connections"] = [
        {
            "connection_id": "notify",
            "connector_type": "notifications",
            "configuration_schema": {"type": "object"},
        }
    ]
    payload["configuration_schema"] = {
        "type": "object",
        "properties": {"destination": {"type": "string"}},
    }
    payload["tests"] = [
        {
            "scenario_id": "smoke",
            "workflow_id": "onboarding",
            "input": {"employee": "Authored fixture"},
        }
    ]
    release_id = release(client, payload)
    response = export(client, release_id)
    files = entries(response.content)
    assert files["activities/" + digest.removeprefix("sha256:") + ".zip"] == source
    frozen = json.loads(files["project.json"])
    for key, value in (
        ProjectContent.model_validate_json(json.dumps(payload)).model_dump(mode="json").items()
    ):
        assert frozen[key] == value
    manifest = json.loads(files["manifest.json"])
    assert manifest["organization_id"] == "acme"
    assert manifest["activity_refs"] == [reference]
    assert response.headers["x-package-digest"] == manifest["package_digest"]
    assert response.headers["content-type"] == "application/zip"
    assert (
        response.headers["content-disposition"] == 'attachment; filename="nexus-flow-project.zip"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    for item in manifest["content_index"]:
        data = files[item["path"]]
        assert item["size"] == len(data)
        assert item["checksum"] == "sha256:" + hashlib.sha256(data).hexdigest()
    assert not {"environment_id", "author_id", "evidence", "configuration_values"} & manifest.keys()
    saved = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "Draft B", "content": content("B")},
    )
    assert saved.status_code == 200, saved.text
    assert export(client, release_id).content == response.content
    assert validate(client, response.content).json() == manifest
    assert validate(client, repack(files)).json() == manifest
    assert backend.projects.project(scope, "hr").head_revision == 2
    assert client.get(BASE + "/projects/hr/activation").json()["release_id"] is None


@pytest.mark.parametrize(
    "mutation", ["tampered", "missing", "extra", "traversal", "duplicate-json"]
)
def test_corrupt_packages_fail_safely_without_mutation(api, mutation):  # noqa: F811
    client, backend, data, _ = api
    release_id = release(client, content())
    files = entries(export(client, release_id).content)
    if mutation == "tampered":
        files["project.json"] += b" "
    elif mutation == "missing":
        del files["project.json"]
    elif mutation == "extra":
        files["private-extra"] = b"private-package-payload"
    elif mutation == "traversal":
        files["../private-package-payload"] = b"bad"
    else:
        files["manifest.json"] = files["manifest.json"].replace(
            b"{", b'{"project_id":"private-package-payload",', 1
        )
    failed = validate(client, repack(files))
    assert failed.status_code == 422, failed.text
    assert "private-package-payload" not in failed.text
    assert failed.headers["cache-control"] == "no-store"
    assert backend.projects.project(data[1], "hr").head_revision == 1
    assert client.get(BASE + "/projects/hr/activation").json()["release_id"] is None


def test_validation_needs_no_catalog_configuration_or_runtime_and_rechecks_authority(api):  # noqa: F811
    client, backend, data, _ = api
    repository, scope, grant, _, token = data
    release_id = release(client, content())
    archive = export(client, release_id).content

    class Unavailable:
        def __getattr__(self, name):
            raise AssertionError("Unrelated provider consulted: " + name)

    isolated = replace(
        backend,
        catalog=Unavailable(),
        intake=Unavailable(),
        configurations=Unavailable(),
        runtime=Unavailable(),
    )
    with TestClient(create_app(isolated), raise_server_exceptions=False) as other:
        other.headers["authorization"] = "Bearer " + token()
        assert validate(other, archive).status_code == 200
    repository.save_grant(
        replace(grant, scope=scope.model_copy(update={"environment_id": "uat"})),
        expected_revision=0,
    )
    assert validate(client, archive, BASE.replace("/dev", "/uat")).status_code == 200
    assert (
        client.get(
            BASE.replace("/dev", "/uat") + "/projects/hr/releases/" + release_id + "/package"
        ).status_code
        == 404
    )
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert validate(client, archive).status_code == 403
    assert client.get(BASE + "/projects/hr/releases/" + release_id + "/package").status_code == 403
    del client.headers["authorization"]
    assert validate(client, archive).status_code == 401


def test_cross_organization_validation_and_production_policy(api):  # noqa: F811
    client, backend, data, _ = api
    repository, scope, grant, _, _ = data
    archive = export(client, release(client, content())).content
    repository.register_binding(
        DirectoryBinding("other", TENANT, "nexus-api", allowed_user_clients=(CLIENT,))
    )
    repository.provision_environments("other")
    repository.add_membership("other", TENANT, "user", USER)
    other_scope = scope.model_copy(update={"organization_id": "other"})
    repository.save_grant(replace(grant, scope=other_scope), expected_revision=0)
    assert validate(client, archive, BASE.replace("/acme/", "/other/")).status_code == 403
    prod = scope.model_copy(update={"environment_id": "prod"})
    repository.save_grant(replace(grant, scope=prod), expected_revision=0)
    prod_base = BASE.replace("/dev", "/prod")
    assert validate(client, archive, prod_base).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod, role="production_support"), expected_revision=1
    )
    assert validate(client, archive, prod_base).status_code == 200
    assert client.get(prod_base + "/projects/hr/releases/missing/package").status_code == 403
    assert backend.projects.list_projects(other_scope) == ()
    assert backend.projects.list_projects(prod) == ()


def test_duplicate_zip_members_and_malformed_media_are_rejected(api):  # noqa: F811
    client, _, _, _ = api
    archive = export(client, release(client, content())).content
    files = entries(archive)
    buffer = io.BytesIO(archive)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(buffer, "a") as opened:
            opened.writestr("project.json", files["project.json"])
    assert validate(client, buffer.getvalue()).status_code == 422
    bad_media = client.post(
        BASE + "/packages/validation", content=archive, headers={"content-type": "application/json"}
    )
    assert bad_media.status_code == 415
    assert (
        client.post(
            BASE + "/packages/validation",
            content=b"private",
            headers={"content-type": "application/zip"},
        ).status_code
        == 422
    )
