"""Real ASGI/SQLite/signed-token requests; explicit runtime/provider fixtures."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import ApiBackend
from nexusflow.contracts import Scope
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.durable_runtime.starts import SQLiteStartRepository
from nexusflow.modules.durable_runtime.types import Snapshot
from nexusflow.modules.identity_environments.service import AdmissionLimiter, IdentityService
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import Capabilities, Configurations, content

BASE = "/api/v1/organizations/acme/environments/dev"


class Runtime:
    def __init__(self):
        self.plans = {}
        self.placements = []
        self.snapshot_calls = []

    async def dispatcher(self, placement):
        self.placements.append(placement)
        return self

    async def start(self, execution_id, plan):
        previous = self.plans.setdefault(execution_id, plan)
        assert previous == plan

    async def snapshot(self, placement, execution_id):
        self.snapshot_calls.append((placement, execution_id))
        plan = self.plans[execution_id]
        return Snapshot(
            plan.binding.release_id,
            plan.definition_digest,
            plan.binding.runtime_version,
            "end",
            "completed",
            0,
            tuple(step.node_id for step in plan.steps),
        )


@pytest.fixture
def api(identity, tmp_path):  # noqa: F811
    identity_repo, scope, grant, keys, token = identity
    projects = SQLiteProjectRepository(tmp_path / "projects.db")
    catalog = SQLiteCatalogRepository(tmp_path / "catalog.db")
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    activations = SQLiteActivationRepository(tmp_path / "activations.db")
    starts = SQLiteStartRepository(tmp_path / "starts.db")
    runtime = Runtime()
    backend = ApiBackend(
        IdentityService(identity_repo, keys, limiter=AdmissionLimiter(maximum=10000)),
        projects,
        catalog,
        intake,
        activations,
        starts,
        Capabilities(),
        Configurations(),
        runtime,
    )
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers["authorization"] = "Bearer " + token()
        yield client, backend, identity, runtime
    for repository in (starts, activations, intake, catalog, projects):
        repository.close()


def create(client, project="hr"):
    return client.post(
        BASE + "/projects", json={"project_id": project, "name": "HR", "content": content()}
    )


def publish_activate(client):
    assert create(client).status_code == 201
    published = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 1, "idempotency_key": "release-a"}
    )
    assert published.status_code == 201, published.text
    release = published.json()["release_id"]
    response = client.post(
        BASE + "/projects/hr/activation",
        json={
            "project_id": "hr",
            "release_id": release,
            "operation_id": "activate-a",
            "configuration_revision": "cfg-1",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    return release


def test_create_edit_validate_publish_activate_start_and_query(api):
    client, backend, _, runtime = api
    assert client.get("/health").json()["status"] == "ok"
    assert client.get(BASE + "/projects").json() == []
    created = create(client)
    assert created.status_code == 201, created.text
    assert created.json()["revision"] == 1
    assert created.json()["content"]["workflows"][0]["workflow_id"] == "onboarding"
    assert client.get(BASE + "/projects").json()[0]["project_id"] == "hr"
    saved = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "Second", "content": content("B")},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    assert client.get(BASE + "/projects/hr?revision=1").json() == created.json()
    validated = client.post(BASE + "/projects/hr/validation", json={"content": content("B")})
    assert validated.status_code == 200 and validated.json() == []
    released = client.post(
        BASE + "/projects/hr/releases", json={"source_revision": 2, "idempotency_key": "r-1"}
    )
    assert released.status_code == 201, released.text
    activation = client.post(
        BASE + "/projects/hr/activation",
        json={
            "project_id": "hr",
            "release_id": released.json()["release_id"],
            "configuration_revision": "cfg-1",
            "operation_id": "activate",
            "expected_revision": 0,
        },
    )
    assert activation.status_code == 200, activation.text
    assert "content_json" not in activation.text
    assert client.get(BASE + "/projects/hr/activation").json() == activation.json()
    payload = {
        "workflow_id": "onboarding",
        "idempotency_key": "run-1",
        "input": {"private": "not-in-status"},
    }
    started = client.post(BASE + "/projects/hr/executions", json=payload)
    assert started.status_code == 202, started.text
    assert client.post(BASE + "/projects/hr/executions", json=payload).json() == started.json()
    assert len(runtime.plans) == 1
    execution = started.json()["execution_id"]
    status = client.get(BASE + "/projects/hr/executions/" + execution)
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "completed"
    assert "not-in-status" not in status.text + started.text
    assert runtime.placements[0] == backend.identity.repository.environments("acme")[0]


@pytest.mark.parametrize("header", [None, "Basic secret", "Bearer invalid", "Bearer "])
def test_missing_or_invalid_authentication_has_no_project_side_effect(api, header):
    client, backend, _, _ = api
    if header is None:
        del client.headers["authorization"]
    else:
        client.headers["authorization"] = header
    response = create(client)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["code"] == "unauthenticated"
    assert backend.projects.list_projects(Scope(organization_id="acme", environment_id="dev")) == ()


def test_errors_are_correlated_and_do_not_echo_inputs(api):
    client, _, _, _ = api
    response = client.post(
        BASE + "/projects",
        json={
            "project_id": "hr",
            "name": "secret-value",
            "content": {},
            "roles": ["production_support"],
        },
        headers={"x-request-id": "caller-controlled"},
    )
    assert response.status_code == 422
    assert "secret-value" not in response.text and "production_support" not in response.text
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.headers["x-request-id"] != "caller-controlled"
    assert response.headers["cache-control"] == "no-store"


def test_scoped_project_lookup_list_and_execution_reads(api):
    client, backend, identity_data, runtime = api
    release = publish_activate(client)
    started = client.post(
        BASE + "/projects/hr/executions",
        json={"workflow_id": "onboarding", "idempotency_key": "run"},
    ).json()
    assert started["release_id"] == release
    repository, scope, grant, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repository.save_grant(replace(grant, scope=uat), expected_revision=0)
    other = BASE.replace("/dev", "/uat")
    assert client.get(other + "/projects").json() == []
    assert client.get(other + "/projects/hr").status_code == 404
    assert (
        client.get(other + "/projects/hr/executions/" + started["execution_id"]).status_code == 404
    )
    assert runtime.snapshot_calls == []
    assert (
        client.get(BASE + "/projects/other/executions/" + started["execution_id"]).status_code
        == 404
    )
    assert client.get(BASE.replace("/acme/", "/other/") + "/projects").status_code == 401


def test_revoked_grants_take_effect_on_next_http_request(api):
    client, _, identity_data, _ = api
    repository, scope, grant, _, _ = identity_data
    assert create(client).status_code == 201
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(BASE + "/projects/hr").status_code == 403
    assert create(client, "other").status_code == 403


def test_conflicts_and_pagination_have_stable_status_codes(api):
    client, _, _, _ = api
    assert create(client).status_code == 201
    assert create(client).status_code == 409
    assert (
        client.put(
            BASE + "/projects/hr",
            json={"expected_revision": 2, "summary": "Stale", "content": content()},
        ).status_code
        == 409
    )
    assert client.get(BASE + "/projects?limit=0").status_code == 422
    assert client.get(BASE + "/projects?limit=1&offset=1").json() == []


def test_api_schema_and_catalog_are_authenticated(api):
    client, _, _, _ = api
    assert client.get(BASE + "/activities").json() == []
    schema = client.get(BASE + "/schema")
    assert schema.status_code == 200, schema.text
    assert schema.json()["info"]["title"] == "Nexus Flow Designer API"
    assert client.get("/docs").status_code == 404
    del client.headers["authorization"]
    assert client.get(BASE + "/schema").status_code == 401


def test_body_limits_and_nonfinite_json_are_rejected(api):
    client, backend, _, _ = api
    with TestClient(
        create_app(backend, maximum_body_bytes=64), raise_server_exceptions=False
    ) as limited:
        response = limited.post(
            BASE + "/projects",
            content="x" * 65,
            headers={"authorization": client.headers["authorization"]},
        )
        assert response.status_code == 413
    response = client.post(
        BASE + "/projects",
        content='{"project_id":"hr","name":"HR","content":{"value":NaN}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert "NaN" not in response.text
    assert (
        client.post(
            BASE + "/projects", content="{}", headers={"content-encoding": "gzip"}
        ).status_code
        == 415
    )


def test_unexpected_errors_do_not_expose_provider_details(api, monkeypatch):
    client, backend, _, _ = api

    def unavailable(*args):
        raise OSError("private-provider-password")

    monkeypatch.setattr(backend.projects, "list_projects", unavailable)
    response = client.get(BASE + "/projects")
    assert response.status_code == 503
    assert "private-provider-password" not in response.text


def test_pending_dispatch_can_be_queried_without_untrusted_remote_lookup(api, monkeypatch):
    client, _, _, runtime = api
    publish_activate(client)
    original = runtime.start

    async def lose_ack(execution, plan):
        await original(execution, plan)
        raise OSError("private endpoint token")

    monkeypatch.setattr(runtime, "start", lose_ack)
    payload = {"workflow_id": "onboarding", "idempotency_key": "lost-ack"}
    response = client.post(BASE + "/projects/hr/executions", json=payload)
    assert response.status_code == 503
    execution = next(iter(runtime.plans))
    status = client.get(BASE + "/projects/hr/executions/" + execution)
    assert status.status_code == 200 and status.json()["state"] == "dispatch_pending"
    assert runtime.snapshot_calls == []
    monkeypatch.setattr(runtime, "start", original)
    assert (
        client.post(BASE + "/projects/hr/executions", json=payload).json()["execution_id"]
        == execution
    )
    assert len(runtime.plans) == 1


def test_runtime_snapshot_must_match_pinned_plan(api, monkeypatch):
    client, _, _, runtime = api
    publish_activate(client)
    execution = client.post(
        BASE + "/projects/hr/executions",
        json={"workflow_id": "onboarding", "idempotency_key": "run"},
    ).json()["execution_id"]
    original = runtime.snapshot

    async def wrong_snapshot(*args):
        return replace(await original(*args), release_id="another-release")

    monkeypatch.setattr(runtime, "snapshot", wrong_snapshot)
    response = client.get(BASE + "/projects/hr/executions/" + execution)
    assert response.status_code == 409
    assert "another-release" not in response.text


def test_prod_business_author_and_task_participant_cannot_deploy_or_start(api):
    client, _, identity_data, runtime = api
    repository, scope, grant, _, _ = identity_data
    prod = scope.model_copy(update={"environment_id": "prod"})
    repository.save_grant(replace(grant, scope=prod), expected_revision=0)
    assert (
        client.post(
            BASE.replace("/dev", "/prod") + "/projects/hr/executions",
            json={"workflow_id": "onboarding", "idempotency_key": "prod"},
        ).status_code
        == 403
    )
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(BASE + "/projects").status_code == 403
    assert (
        client.post(
            BASE + "/projects/hr/executions",
            json={"workflow_id": "onboarding", "idempotency_key": "run"},
        ).status_code
        == 403
    )
    assert runtime.placements == []


def test_request_body_cannot_override_scope_or_temporal_placement(api):
    client, _, _, runtime = api
    publish_activate(client)
    response = client.post(
        BASE + "/projects/hr/executions",
        json={
            "workflow_id": "onboarding",
            "idempotency_key": "run",
            "task_queue": "attacker",
            "organization_id": "other",
        },
    )
    assert response.status_code == 422
    assert runtime.placements == []
    assert "attacker" not in response.text


def test_chunked_body_is_bounded_without_content_length(api):
    client, backend, _, _ = api
    with TestClient(
        create_app(backend, maximum_body_bytes=64), raise_server_exceptions=False
    ) as limited:
        response = limited.post(
            BASE + "/projects",
            content=iter([b"x" * 40, b"y" * 40]),
            headers={"authorization": client.headers["authorization"]},
        )
        assert response.status_code == 413


def test_unknown_internal_errors_have_no_traceback_or_input_leak(api, monkeypatch):
    client, backend, _, _ = api

    def fail(*args):
        raise RuntimeError("secret debug details")

    monkeypatch.setattr(backend.projects, "list_projects", fail)
    response = client.get(BASE + "/projects")
    assert response.status_code == 500
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert "secret debug details" not in response.text


def test_generated_schema_declares_bearer_authentication(api):
    client, _, _, _ = api
    document = client.get(BASE + "/schema").json()
    assert document["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    path = "/api/v1/organizations/{organization_id}/environments/{environment_id}/projects"
    assert document["paths"][path]["get"]["security"] == [{"HTTPBearer": []}]
