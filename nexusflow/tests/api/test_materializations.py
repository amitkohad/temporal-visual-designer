"""HTTP materialization contracts and provider failure/retry boundaries."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_package_imports import imports, stage  # noqa: F401


def request(**changes):
    return {
        "operation_id": "materialize-one",
        "expected_project_revision": 1,
        "configuration_revision": "cfg-1",
        **changes,
    }


PATH = BASE + "/package-imports/first/materializations"


def test_materialization_receipt_strict_schema_and_no_activation(imports):  # noqa: F811
    client, backend, data, runtime, archive = imports
    assert stage(client, archive).status_code == 201
    response = client.post(PATH, json=request())
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["state"] == "materialized"
    assert receipt["revision"] == 2
    assert receipt["release_id"] != receipt["source_release_id"]
    assert client.get(PATH + "/materialize-one").json() == receipt
    assert client.get(BASE + "/package-imports/first").json()["state"] == "staged"
    assert backend.activations.current(data[1], "hr") is None
    assert not runtime.plans
    assert client.get(PATH.replace("first", "other") + "/materialize-one").status_code == 404
    assert client.post(PATH, json=request(actor_id="spoof")).status_code == 422
    assert client.post(PATH, json=request(expected_project_revision=True)).status_code == 422
    assert client.post(PATH, json=request(runtime_version="arbitrary")).status_code == 422
    spec = create_app(backend).openapi()
    path = "/api/v1/organizations/{organization_id}/environments/{environment_id}/package-imports/{import_operation_id}/materializations"
    assert spec["paths"][path]["post"]["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("MaterializationReceipt")


def test_receipt_retry_works_when_stage_and_configuration_fail(imports, monkeypatch):  # noqa: F811
    client, backend, data, _, archive = imports
    assert stage(client, archive).status_code == 201
    first = client.post(PATH, json=request())
    assert first.status_code == 201, first.text
    monkeypatch.setattr(backend.package_imports, "download", Mock(side_effect=OSError("private")))
    backend.configurations.available = False
    assert client.post(PATH, json=request()).json() == first.json()
    assert client.get(PATH + "/materialize-one").json() == first.json()
    assert client.post(PATH, json=request(configuration_revision="changed")).status_code == 409
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.post(PATH, json=request()).status_code == 403
    assert client.get(PATH + "/materialize-one").status_code == 403


def test_failed_materialization_does_not_create_a_revision(imports, monkeypatch):  # noqa: F811
    client, backend, data, _, archive = imports
    assert stage(client, archive).status_code == 201
    backend.configurations.available = False
    assert client.post(PATH, json=request()).status_code == 422
    assert backend.projects.project(data[1], "hr").head_revision == 1
    assert client.get(PATH + "/materialize-one").status_code == 404
    backend.configurations.available = True
    monkeypatch.setattr(backend.package_imports, "download", Mock(side_effect=OSError("private")))
    failed = client.post(PATH, json=request())
    assert failed.status_code == 503 and "private" not in failed.text
    assert backend.projects.project(data[1], "hr").head_revision == 1


def test_ambiguous_commit_retry_reads_original_receipt(imports, monkeypatch):  # noqa: F811
    client, backend, _, _, archive = imports
    assert stage(client, archive).status_code == 201
    original = backend.projects.commit_materialization

    def lose_ack(*args):
        original(*args)
        raise OSError("Response lost")

    monkeypatch.setattr(backend.projects, "commit_materialization", lose_ack)
    assert client.post(PATH, json=request()).status_code == 503
    retry = client.post(PATH, json=request())
    assert retry.status_code == 201, retry.text
    assert retry.json()["revision"] == 2


def test_provider_unconfigured_authorizes_first(api):  # noqa: F811
    client, backend, data, _ = api
    assert client.post(PATH, json=request()).status_code == 503
    assert client.get(PATH + "/materialize-one").status_code == 503
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.post(PATH, json=request()).status_code == 403
    assert client.get(PATH + "/materialize-one").status_code == 403


def test_structurally_unsupported_project_repository_fails_closed(imports):  # noqa: F811
    original, backend, _, _, _ = imports
    configured = replace(backend, projects=object())
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        response = client.post(PATH, json=request())
        assert response.status_code == 503, response.text


@pytest.mark.parametrize(
    "case", ["matching", "missing", "withdrawn", "metadata", "bytes", "worker"]
)
def test_destination_artifact_approval_and_identity_gates(imports, monkeypatch, case):  # noqa: F811
    from nexusflow.modules.activity_catalog.catalog_types import ExportedActivity
    from nexusflow.modules.activity_catalog.types import ImportedBundle
    from nexusflow.modules.package_promotion.package_format import encode_package
    from nexusflow.tests.package_promotion.test_package_format import release_with_activity

    client, backend, data, _, _ = imports
    source, artifacts = release_with_activity()
    artifact = artifacts[0]
    package = encode_package(source, artifacts)
    assert stage(client, package.archive).status_code == 201
    if case != "missing":
        backend.catalog.publish(
            ImportedBundle(
                data[1],
                artifact.reference.artifact_digest,
                artifact.manifest_json,
                "publisher",
                "2026-10-25T00:00:00Z",
            ),
            artifact.archive,
            "publisher",
        )
    if case == "withdrawn":
        backend.catalog.transition(data[1], artifact.reference, "withdrawn", 1, "publisher")
    if case == "metadata":
        original = backend.catalog.get(data[1], artifact.reference)
        import json

        metadata = json.loads(original.manifest_json)
        metadata["descriptor"]["display_name"] = "Changed"
        monkeypatch.setattr(
            backend.catalog,
            "get",
            lambda *args: replace(original, manifest_json=json.dumps(metadata)),
        )
    if case == "bytes":
        monkeypatch.setattr(
            backend.catalog,
            "export",
            lambda *args: ExportedActivity(artifact.reference, artifact.manifest_json, b"corrupt"),
        )
    if case == "worker":
        backend.capabilities.available = False
    result = client.post(PATH, json=request())
    if case == "matching":
        assert result.status_code == 201, result.text
        assert backend.projects.project(data[1], "hr").head_revision == 2
    else:
        assert result.status_code == 422, result.text
        assert backend.projects.project(data[1], "hr").head_revision == 1
        assert client.get(PATH + "/materialize-one").status_code == 404
