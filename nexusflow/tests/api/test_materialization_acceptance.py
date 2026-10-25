"""Independent signed HTTP checks for staged project materialization."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.package_imports import SQLitePackageImportRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_package_imports import stage
from nexusflow.tests.api.test_project_packages import download, publish
from nexusflow.tests.package_promotion.test_activation import content

UAT = BASE.replace("/dev", "/uat")
PROD = BASE.replace("/dev", "/prod")


def request(operation="install", expected=0):
    return {
        "operation_id": operation,
        "expected_project_revision": expected,
        "configuration_revision": "cfg-1",
        "runtime_version": "proof.v1",
    }


@pytest.fixture
def materialization(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    source_release = publish(original)
    archive = download(original, source_release).content
    identity_repo, _, grant, _, _ = data
    uat = Scope(organization_id="acme", environment_id="uat")
    identity_repo.save_grant(replace(grant, scope=uat), expected_revision=0)
    imports = SQLitePackageImportRepository(str(tmp_path / "materialization-imports.db"))
    configured = replace(backend, package_imports=imports)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert stage(client, archive, base=UAT).status_code == 201
        yield client, configured, data, runtime, archive, source_release
    imports.close()


def materialize(client, body=None, base=UAT, stage_id="first"):
    return client.post(
        base + f"/package-imports/{stage_id}/materializations",
        json=request() if body is None else body,
    )


def test_target_snapshot_requires_explicit_activation_and_start(materialization):
    client, backend, data, runtime, _, source_release = materialization
    scope = Scope(organization_id="acme", environment_id="uat")
    staged = client.get(UAT + "/package-imports/first").json()
    result = materialize(client)
    assert result.status_code == 201, result.text
    receipt = result.json()
    assert receipt["state"] == "materialized"
    assert receipt["source_release_id"] == source_release
    assert receipt["release_id"] != source_release
    assert receipt["revision"] == 1
    assert receipt["actor_id"]
    source = backend.projects.release(data[1], "hr", source_release)
    target = backend.projects.release(scope, "hr", receipt["release_id"])
    assert (
        target.content_json,
        target.artifacts_json,
        target.content_digest,
        target.executable_digest,
    ) == (
        source.content_json,
        source.artifacts_json,
        source.content_digest,
        source.executable_digest,
    )
    assert backend.projects.project(scope, "hr").head_revision == 1
    assert backend.activations.current(scope, "hr") is None
    assert not runtime.plans
    assert client.get(UAT + "/package-imports/first").json() == staged
    assert client.get(UAT + "/package-imports/first/materializations/install").json() == receipt
    activation = client.post(
        UAT + "/projects/hr/activation",
        json={
            "project_id": "hr",
            "release_id": receipt["release_id"],
            "configuration_revision": "cfg-1",
            "operation_id": "activate-import",
            "expected_revision": 0,
        },
    )
    assert activation.status_code == 200, activation.text
    started = client.post(
        UAT + "/projects/hr/executions",
        json={
            "workflow_id": "onboarding",
            "idempotency_key": "target-run",
        },
    )
    assert started.status_code == 202, started.text
    assert started.json()["release_id"] == receipt["release_id"]


def test_retry_ignores_advanced_head_and_unavailable_readiness(materialization):
    client, backend, _, _, _, _ = materialization
    first = materialize(client)
    assert first.status_code == 201, first.text
    second = materialize(client, request("second", 1))
    assert second.status_code == 201, second.text
    backend.configurations.available = False
    assert materialize(client).json() == first.json()
    changed = request()
    changed["configuration_revision"] = "different"
    assert materialize(client, changed).status_code == 409
    assert materialize(client, stage_id="missing").status_code == 409


def test_target_configuration_failure_and_stale_head_leave_no_partial_project(materialization):
    client, backend, _, _, _, _ = materialization
    scope = Scope(organization_id="acme", environment_id="uat")
    backend.configurations.available = False
    result = materialize(client)
    assert result.status_code == 422, result.text
    assert backend.projects.list_projects(scope) == ()
    assert backend.activations.current(scope, "hr") is None
    backend.configurations.available = True
    assert materialize(client, request(expected=1)).status_code == 409
    assert backend.projects.list_projects(scope) == ()
    assert materialize(client).status_code == 201
    assert materialize(client, request("stale", 0)).status_code == 409
    assert backend.projects.project(scope, "hr").head_revision == 1


def test_prod_support_can_materialize_without_authoring_or_export_permission(materialization):
    client, backend, data, runtime, archive, _ = materialization
    identity_repo, _, grant, _, token = data
    scope = Scope(organization_id="acme", environment_id="prod")
    identity_repo.save_grant(replace(grant, scope=scope), expected_revision=0)
    assert stage(client, archive, base=PROD).status_code == 403
    assert materialize(client, base=PROD).status_code == 403
    client.headers["authorization"] = "Bearer " + token(roles=["production_support"])
    assert materialize(client, base=PROD).status_code == 403
    identity_repo.save_grant(
        replace(grant, scope=scope, role="production_support"), expected_revision=1
    )
    assert stage(client, archive, base=PROD).status_code == 201
    response = materialize(client, base=PROD)
    assert response.status_code == 201, response.text
    release_id = response.json()["release_id"]
    assert client.get(PROD + f"/projects/hr/releases/{release_id}/package").status_code == 403
    assert (
        client.put(
            PROD + "/projects/hr",
            json={"expected_revision": 1, "summary": "Denied", "content": content()},
        ).status_code
        == 403
    )
    assert backend.activations.current(scope, "hr") is None
    assert not runtime.plans


def test_receipt_rechecks_revocation_and_never_crosses_scope(materialization):
    client, backend, data, _, _, _ = materialization
    first = materialize(client)
    assert first.status_code == 201, first.text
    path = "/package-imports/first/materializations/install"
    assert client.get(BASE + path).status_code == 404
    assert client.get(UAT + "/package-imports/other/materializations/install").status_code == 404
    identity_repo, _, grant, _, _ = data
    scope = Scope(organization_id="acme", environment_id="uat")
    identity_repo.save_grant(
        replace(grant, scope=scope, role="task_participant"), expected_revision=1
    )
    assert client.get(UAT + path).status_code == 403
    assert materialize(client).status_code == 403
    assert backend.projects.project(scope, "hr").head_revision == 1


@pytest.mark.parametrize(
    "extra", [{"actor_id": "spoof"}, {"activate": True}, {"scope": {"environment_id": "prod"}}]
)
def test_untrusted_command_fields_cannot_change_target_author_or_activate(materialization, extra):
    client, backend, _, _, _, _ = materialization
    assert materialize(client, {**request(), **extra}).status_code == 422
    assert backend.projects.list_projects(Scope(organization_id="acme", environment_id="uat")) == ()
