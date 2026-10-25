"""Independent signed HTTP acceptance for destination configuration snapshots."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts import Scope
from nexusflow.modules.package_promotion.configurations import SQLiteConfigurationRepository
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_materialization_acceptance import UAT, materialize
from nexusflow.tests.api.test_package_imports import stage
from nexusflow.tests.api.test_project_packages import download
from nexusflow.tests.package_promotion.test_activation import content

SCHEMA = {
    "type": "object",
    "properties": {"destination": {"type": "string", "minLength": 1}},
    "required": ["destination"],
    "additionalProperties": False,
}
VALUES = {"destination": "sensitive-target-setting"}


@pytest.fixture
def destination(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    project = content()
    project["configuration_schema"] = SCHEMA
    created = original.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project}
    )
    assert created.status_code == 201, created.text
    released = original.post(
        BASE + "/projects/hr/releases", json={"source_revision": 1, "idempotency_key": "release"}
    )
    assert released.status_code == 201, released.text
    exported = download(original, released.json()["release_id"])
    assert exported.status_code == 200, exported.text
    repository, _, grant, _, _ = data
    scope = Scope(organization_id="acme", environment_id="uat")
    repository.save_grant(replace(grant, scope=scope), expected_revision=0)
    configs = SQLiteConfigurationRepository(tmp_path / "configuration-acceptance.db")
    imports = SQLitePackageImportRepository(tmp_path / "configuration-imports.db")
    configured = replace(backend, configurations=configs, package_imports=imports)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert stage(client, exported.content, base=UAT).status_code == 201
        yield client, configured, data, runtime, exported.content, configs
    imports.close()
    configs.close()


def administrator(data, scope, revision):
    repository, _, grant, _, _ = data
    repository.save_grant(
        replace(grant, scope=scope, role="organization_administrator"), expected_revision=revision
    )


def author(data, scope, revision):
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, scope=scope), expected_revision=revision)


def put_configuration(client, values=VALUES, base=UAT, revision="cfg-1"):
    return client.put(base + "/projects/hr/configurations/" + revision, json={"values": values})


def test_destination_configuration_is_required_before_project_materialization(destination):
    client, backend, data, runtime, archive, configs = destination
    scope = Scope(organization_id="acme", environment_id="uat")
    rejected = materialize(client)
    assert rejected.status_code == 422, rejected.text
    assert backend.projects.list_projects(scope) == ()
    assert backend.activations.current(scope, "hr") is None
    assert put_configuration(client).status_code == 403
    administrator(data, scope, 1)
    created = put_configuration(client)
    assert created.status_code == 201, created.text
    assert created.json()["configuration_revision"] == "cfg-1"
    assert created.json()["revision"] == 1
    assert "values" not in created.json()
    assert VALUES["destination"] not in created.text
    assert backend.projects.list_projects(scope) == ()
    assert configs.supports(scope, "hr", "cfg-1", SCHEMA)
    assert not configs.supports(data[1], "hr", "cfg-1", SCHEMA)
    assert not configs.supports(scope, "other", "cfg-1", SCHEMA)
    assert client.get(UAT + "/projects/hr/configurations/cfg-1").json() == created.json()
    listing = client.get(UAT + "/projects/hr/configurations")
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"] == [created.json()]
    assert VALUES["destination"] not in listing.text
    author(data, scope, 2)
    result = materialize(client)
    assert result.status_code == 201, result.text
    assert backend.activations.current(scope, "hr") is None
    activation = client.post(
        UAT + "/projects/hr/activation",
        json={
            "project_id": "hr",
            "release_id": result.json()["release_id"],
            "configuration_revision": "cfg-1",
            "operation_id": "activate",
            "expected_revision": 0,
        },
    )
    assert activation.status_code == 200, activation.text
    assert activation.json()["configuration_revision"] == "cfg-1"
    started = client.post(
        UAT + "/projects/hr/executions",
        json={"workflow_id": "onboarding", "idempotency_key": "target-run"},
    )
    assert started.status_code == 202, started.text
    assert runtime.plans
    assert VALUES["destination"].encode() not in archive


def test_schema_mismatch_is_rejected_without_target_writes(destination):
    client, backend, data, _, _, configs = destination
    scope = Scope(organization_id="acme", environment_id="uat")
    administrator(data, scope, 1)
    created = put_configuration(client, {"destination": 4})
    assert created.status_code == 201, created.text
    assert not configs.supports(scope, "hr", "cfg-1", SCHEMA)
    assert not configs.supports(scope, "hr", "cfg-1", {"$ref": "https://invalid.example/schema"})
    author(data, scope, 2)
    rejected = materialize(client)
    assert rejected.status_code == 422, rejected.text
    assert backend.projects.list_projects(scope) == ()
    assert backend.activations.current(scope, "hr") is None


def test_configuration_retry_is_immutable_and_rechecks_permission(destination, tmp_path):
    client, _, data, _, _, configs = destination
    scope = Scope(organization_id="acme", environment_id="uat")
    administrator(data, scope, 1)
    first = put_configuration(client)
    assert first.status_code == 201, first.text
    assert put_configuration(client).json() == first.json()
    assert put_configuration(client, {"destination": "changed"}).status_code == 409
    assert client.get(BASE + "/projects/hr/configurations/cfg-1").status_code == 403
    reopened = SQLiteConfigurationRepository(tmp_path / "configuration-acceptance.db")
    try:
        assert reopened.supports(scope, "hr", "cfg-1", SCHEMA)
        assert reopened.get(scope, "hr", "cfg-1").metadata.model_dump(mode="json") == first.json()
    finally:
        reopened.close()
    author(data, scope, 2)
    assert put_configuration(client).status_code == 403
    assert client.get(UAT + "/projects/hr/configurations/cfg-1").status_code == 403
    assert client.get(UAT + "/projects/hr/configurations").status_code == 403
    assert configs.supports(scope, "hr", "cfg-1", SCHEMA)
