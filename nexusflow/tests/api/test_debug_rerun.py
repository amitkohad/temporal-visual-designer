"""Signed explicit reruns preserve parent snapshots and immutable retry identity."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, debugging  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content


def rerun_path(session):
    return PATH + "/" + session["session_id"] + "/reruns"


def payload(**changes):
    return dict(PAYLOAD, operation_id="rerun-1", **changes)


@pytest.fixture
def rerunning(debugging):  # noqa: F811
    client, backend, data, runtime = debugging
    response = client.post(PATH, json=dict(PAYLOAD, input={"private": "parent-only-secret"}))
    assert response.status_code == 202, response.text
    return client, backend, data, runtime, response.json()


def test_rerun_uses_explicit_new_revision_inputs_and_fresh_execution(rerunning):
    client, backend, data, runtime, parent = rerunning
    saved = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "New draft", "content": content("B")},
    )
    assert saved.status_code == 200, saved.text
    body = payload(source_revision=2, input={"employee": "new-input"})
    response = client.post(rerun_path(parent), json=body)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    child = response.json()
    assert child["source_revision"] == 2
    assert child["workflow_id"] == parent["workflow_id"]
    assert child["session_id"] != parent["session_id"]
    assert child["execution_id"] != parent["execution_id"]
    assert child["definition_digest"] != parent["definition_digest"]
    assert "parent-only-secret" not in response.text and "new-input" not in response.text
    original = backend.debug_sessions.operation(data[1], "hr", PAYLOAD["operation_id"])
    rerun = backend.debug_sessions.operation(data[1], "hr", body["operation_id"])
    assert original.session.model_dump(mode="json") == parent
    assert original.request.input == {"private": "parent-only-secret"}
    assert json.loads(rerun.plan.input_json) == {"employee": "new-input"}
    assert runtime.calls == []  # No query/control of the parent's live execution.
    assert backend.activations.current(data[1], "hr") is None


def test_omitted_rerun_inputs_do_not_copy_private_parent_values(rerunning):
    client, backend, data, _, parent = rerunning
    response = client.post(rerun_path(parent), json=payload())
    assert response.status_code == 202, response.text
    record = backend.debug_sessions.operation(data[1], "hr", "rerun-1")
    assert record.request.input == {}
    assert json.loads(record.plan.input_json) == {}


def test_exact_rerun_retry_uses_saved_child_without_readiness_or_runtime(rerunning, monkeypatch):
    client, backend, _, runtime, parent = rerunning
    path = rerun_path(parent)
    first = client.post(path, json=payload())
    assert first.status_code == 202, first.text
    backend.configurations.available = False

    async def unavailable(*_args):
        raise OSError("sensitive-runtime-detail")

    monkeypatch.setattr(runtime, "start", unavailable)
    retry = client.post(path, json=payload())
    assert retry.status_code == 202, retry.text
    assert retry.json() == first.json()
    assert client.post(path, json=payload(input={"changed": True})).status_code == 409


def test_rerun_operation_is_bound_to_parent_and_cannot_be_normal_start(rerunning):
    client, _, _, _, parent = rerunning
    child = client.post(rerun_path(parent), json=payload())
    assert child.status_code == 202, child.text
    assert client.post(PATH, json=payload()).status_code == 409
    other = client.post(PATH, json=dict(PAYLOAD, operation_id="other-parent"))
    assert other.status_code == 202, other.text
    assert client.post(rerun_path(other.json()), json=payload()).status_code == 409


def test_rerun_retry_survives_repository_reopen(rerunning):
    client, backend, _, runtime, parent = rerunning
    response = client.post(rerun_path(parent), json=payload())
    assert response.status_code == 202, response.text
    database = backend.debug_sessions.connection.execute("PRAGMA database_list").fetchone()[2]
    backend.debug_sessions.close()
    restored = SQLiteDebugSessionRepository(database)
    try:
        configured = replace(backend, debug_sessions=restored)
        with TestClient(create_app(configured), raise_server_exceptions=False) as recovered:
            recovered.headers.update(client.headers)
            retry = recovered.post(rerun_path(parent), json=payload())
            assert retry.status_code == 202, retry.text
            assert retry.json() == response.json()
            assert recovered.post(PATH, json=payload()).status_code == 409
        assert runtime.calls == []
    finally:
        restored.close()


def test_rerun_cannot_select_another_workflow(rerunning):
    client, backend, data, _, parent = rerunning
    response = client.post(rerun_path(parent), json=payload(workflow_id="purchase"))
    assert response.status_code == 409, response.text
    assert backend.debug_sessions.operation(data[1], "hr", "rerun-1") is None


def test_existing_normal_operation_cannot_be_adopted_as_rerun(rerunning):
    client, _, _, _, parent = rerunning
    assert client.post(PATH, json=payload()).status_code == 202
    assert client.post(rerun_path(parent), json=payload()).status_code == 409
    assert client.post(rerun_path(parent), json=PAYLOAD).status_code == 409


def test_rerun_requires_parent_owner_and_fresh_permission_even_on_retry(rerunning):
    client, _, data, runtime, parent = rerunning
    path = rerun_path(parent)
    assert client.post(path, json=payload()).status_code == 202
    client.headers["authorization"] = "Bearer " + data[4](sub="another-pairwise-subject")
    assert client.post(path, json=payload()).status_code == 403
    client.headers["authorization"] = "Bearer " + data[4]()
    repository, scope, grant, _, _ = data
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.post(path, json=payload()).status_code == 403
    assert runtime.calls == []


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_rerun_is_dev_only_before_provider_access(rerunning, environment):
    client, backend, data, runtime, parent = rerunning
    repository, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    repository.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    disabled = replace(backend, debug_sessions=None, debug_runtime=None)
    with TestClient(create_app(disabled), raise_server_exceptions=False) as other:
        other.headers.update(client.headers)
        response = other.post(
            rerun_path(parent).replace("/dev/", f"/{environment}/"), json=payload()
        )
    assert response.status_code == 403, response.text
    assert runtime.calls == []


@pytest.mark.parametrize(
    "changes",
    [{"mode": "mock"}, {"source_revision": 0}, {"actor_id": "spoof"}, {"operation_id": ""}],
)
def test_rerun_payload_is_strict(rerunning, changes):
    client, _, _, runtime, parent = rerunning
    body = payload()
    body.update(changes)
    assert client.post(rerun_path(parent), json=body).status_code == 422
    assert runtime.calls == []


def test_rerun_requires_explicit_mode_and_existing_parent(rerunning):
    client, _, _, _, parent = rerunning
    body = payload()
    del body["mode"]
    assert client.post(rerun_path(parent), json=body).status_code == 422
    assert client.post(PATH + "/missing/reruns", json=payload()).status_code == 404


def test_pending_parent_cannot_be_rerun(rerunning, monkeypatch):
    client, backend, data, runtime, _ = rerunning

    async def unavailable(*_args):
        raise OSError("sensitive-runtime-detail")

    monkeypatch.setattr(runtime, "start", unavailable)
    failed = client.post(PATH, json=dict(PAYLOAD, operation_id="pending-parent"))
    assert failed.status_code == 503, failed.text
    pending = backend.debug_sessions.operation(data[1], "hr", "pending-parent")
    response = client.post(rerun_path(pending.session.model_dump()), json=payload())
    assert response.status_code == 409, response.text
