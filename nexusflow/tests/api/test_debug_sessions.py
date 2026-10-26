"""Signed HTTP debug authorization, isolated intents and metadata-only responses."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.durable_runtime.types import CommandReceipt, Snapshot
from nexusflow.tests.api.test_api import BASE, api, create, identity  # noqa: F401

PATH = BASE + "/projects/hr/debug-sessions"
PAYLOAD = {
    "operation_id": "debug-a",
    "source_revision": 1,
    "workflow_id": "onboarding",
    "configuration_revision": "cfg-1",
    "mode": "real",
}


class DebugRuntime:
    def __init__(self):
        self.plans = {}
        self.calls = []

    async def start(self, placement, execution_id, plan):
        self.plans[execution_id] = plan
        return "exact-run"

    async def snapshot(self, placement, execution_id, run_id):
        self.calls.append((execution_id, run_id))
        plan = self.plans[execution_id]
        return Snapshot(
            plan.binding.release_id, plan.definition_digest, "proof.v1", "start", "paused", 0, ()
        )

    async def control(self, placement, execution_id, run_id, command):
        self.calls.append((execution_id, run_id))
        return CommandReceipt(command.command_id, 1)


@pytest.fixture
def debugging(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    runtime = DebugRuntime()
    backend = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert create(client).status_code == 201
        yield client, backend, data, runtime
    sessions.close()


def test_start_retry_status_control_and_private_input_redaction(debugging):
    client, backend, data, runtime = debugging
    payload = dict(PAYLOAD, input={"private": "debug-secret"})
    response = client.post(PATH, json=payload)
    assert response.status_code == 202, response.text
    session = response.json()
    assert session["temporal_run_id"] == "exact-run" and "debug-secret" not in response.text
    assert client.post(PATH, json=payload).json() == session
    path = PATH + "/" + session["session_id"]
    status = client.get(path)
    assert status.status_code == 200, status.text
    assert status.json()["snapshot"]["state"] == "paused"
    control = client.post(
        path + "/commands",
        json={
            "command_id": "step",
            "node_id": "start",
            "action": "step_over",
            "expected_revision": 0,
        },
    )
    assert control.status_code == 200 and control.json()["revision"] == 1
    assert all(run == "exact-run" for _, run in runtime.calls)
    assert backend.activations.current(data[1], "hr") is None


def test_revoked_grant_blocks_every_session_operation(debugging):
    client, _, data, _ = debugging
    response = client.post(PATH, json=PAYLOAD)
    assert response.status_code == 202, response.text
    path = PATH + "/" + response.json()["session_id"]
    repo, scope, grant, _, _ = data
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(path).status_code == 403
    assert client.post(PATH, json=PAYLOAD).status_code == 403
    assert (
        client.post(
            path + "/commands",
            json={
                "command_id": "step",
                "node_id": "start",
                "action": "continue",
                "expected_revision": 0,
            },
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "payload",
    [
        {k: v for k, v in PAYLOAD.items() if k != "mode"},
        dict(PAYLOAD, mode="simulated"),
        dict(PAYLOAD, actor_id="spoof"),
        dict(PAYLOAD, source_revision=0),
    ],
)
def test_debug_request_is_strict(debugging, payload):
    client, _, _, runtime = debugging
    assert client.post(PATH, json=payload).status_code == 422
    assert runtime.plans == {}


def test_missing_provider_checked_after_fresh_authorization(api):  # noqa: F811
    client, _, data, _ = api
    assert client.post(PATH, json=PAYLOAD).status_code == 503
    repo, scope, grant, _, _ = data
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.post(PATH, json=PAYLOAD).status_code == 403


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_is_forbidden_even_for_admin_before_missing_provider(api, environment):  # noqa: F811
    client, _, data, _ = api
    repo, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    repo.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    assert client.post(PATH.replace("/dev/", f"/{environment}/"), json=PAYLOAD).status_code == 403


def test_session_owner_is_checked_on_every_access(debugging):
    client, _, data, _ = debugging
    response = client.post(PATH, json=PAYLOAD)
    assert response.status_code == 202, response.text
    session_id = response.json()["session_id"]
    client.headers["authorization"] = "Bearer " + data[4](sub="other-pairwise-subject")
    assert client.get(PATH + "/" + session_id).status_code == 403
    assert client.post(PATH, json=PAYLOAD).status_code == 403
    assert (
        client.post(
            PATH + "/" + session_id + "/commands",
            json={
                "command_id": "step",
                "node_id": "start",
                "action": "continue",
                "expected_revision": 0,
            },
        ).status_code
        == 403
    )


def test_changed_command_after_success_is_conflict(debugging):
    client, _, _, _ = debugging
    response = client.post(PATH, json=PAYLOAD)
    assert response.status_code == 202, response.text
    path = PATH + "/" + response.json()["session_id"] + "/commands"
    command = {
        "command_id": "step",
        "node_id": "start",
        "action": "step_over",
        "expected_revision": 0,
    }
    first = client.post(path, json=command)
    assert first.status_code == 200, first.text
    assert client.post(path, json=command).json() == first.json()
    assert client.post(path, json=dict(command, action="continue")).status_code == 409
