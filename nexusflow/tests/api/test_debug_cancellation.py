"""Signed cancellation command boundaries; Temporal semantics have separate real-run tests."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import CommandReceipt
from nexusflow.tests.api.test_api import api, create, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime

CANCEL = {
    "command_id": "cancel-paused",
    "node_id": "start",
    "action": "cancel",
    "expected_revision": 0,
}


class CancellationRuntime(DebugRuntime):
    """An exact-run fake port, not evidence of worker cancellation semantics."""

    def __init__(self):
        super().__init__()
        self.commands = []
        self.available = True
        self.state = "paused"

    async def snapshot(self, placement, execution_id, run_id):
        if not self.available:
            raise DebugError("unavailable", "Runtime is unavailable")
        return replace(await super().snapshot(placement, execution_id, run_id), state=self.state)

    async def control(self, placement, execution_id, run_id, command):
        self.commands.append((placement, execution_id, run_id, command))
        if self.state != "paused" or command.node_id != "start" or command.expected_revision != 0:
            raise DebugError("conflict", "Cancellation requires the current paused step")
        assert command.action == "cancel"
        self.state = "cancelled"
        return CommandReceipt(command.command_id, 1)


@pytest.fixture
def cancellation(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    repository = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    runtime = CancellationRuntime()
    backend = replace(backend, debug_sessions=repository, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert create(client).status_code == 201
        response = client.post(PATH, json=PAYLOAD)
        assert response.status_code == 202, response.text
        session = response.json()
        yield client, backend, data, runtime, session
    repository.close()


def commands(session):
    return PATH + "/" + session["session_id"] + "/commands"


def test_cancel_is_bound_to_exact_run_and_retry_does_not_contact_runtime(cancellation):
    client, backend, data, runtime, session = cancellation
    path = commands(session)
    response = client.post(path, json=CANCEL)
    assert response.status_code == 200, response.text
    assert response.json() == {"command_id": CANCEL["command_id"], "revision": 1}
    placement, execution, run_id, command = runtime.commands[0]
    assert placement.scope == data[1]
    assert (execution, run_id) == (session["execution_id"], session["temporal_run_id"])
    assert command.action == "cancel"
    assert backend.activations.current(data[1], "hr") is None
    runtime.available = False
    assert client.post(path, json=CANCEL).json() == response.json()
    assert len(runtime.commands) == 1


def test_cancel_receipt_survives_repository_reopen(cancellation, tmp_path):
    client, backend, _, runtime, session = cancellation
    path = commands(session)
    response = client.post(path, json=CANCEL)
    assert response.status_code == 200, response.text
    backend.debug_sessions.close()
    runtime.available = False
    repository = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        with TestClient(
            create_app(replace(backend, debug_sessions=repository)),
            raise_server_exceptions=False,
        ) as restarted:
            restarted.headers.update(client.headers)
            retry = restarted.post(path, json=CANCEL)
            assert retry.status_code == 200, retry.text
            assert retry.json() == response.json()
            assert len(runtime.commands) == 1
    finally:
        repository.close()


@pytest.mark.parametrize(
    "change", [{"action": "continue"}, {"node_id": "other"}, {"expected_revision": 1}]
)
def test_cancel_command_identity_is_immutable(cancellation, change):
    client, _, _, runtime, session = cancellation
    path = commands(session)
    assert client.post(path, json=CANCEL).status_code == 200
    assert client.post(path, json=dict(CANCEL, **change)).status_code == 409
    assert len(runtime.commands) == 1


@pytest.mark.parametrize("state", ["running", "waiting_approval", "completed", "cancelled"])
def test_runtime_rejection_is_safe_conflict(cancellation, state):
    client, _, _, runtime, session = cancellation
    runtime.state = state
    response = client.post(commands(session), json=CANCEL)
    assert response.status_code == 409, response.text


def test_cancel_is_owner_scoped(cancellation):
    client, _, data, runtime, session = cancellation
    client.headers["authorization"] = "Bearer " + data[4](sub="other-pairwise-subject")
    assert client.post(commands(session), json=CANCEL).status_code == 403
    assert runtime.calls == [] and runtime.commands == []


def test_revocation_blocks_cancel_and_saved_receipt_retries(cancellation):
    client, _, data, runtime, session = cancellation
    path = commands(session)
    assert client.post(path, json=CANCEL).status_code == 200
    repo, scope, grant, _, _ = data
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.post(path, json=CANCEL).status_code == 403
    assert len(runtime.commands) == 1


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_rejects_cancellation_before_provider_access(cancellation, environment):
    client, _, data, runtime, session = cancellation
    repo, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    repo.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    path = commands(session).replace("/dev/", f"/{environment}/")
    assert client.post(path, json=CANCEL).status_code == 403
    assert runtime.calls == [] and runtime.commands == []


@pytest.mark.parametrize(
    "change",
    [
        {"action": "terminate"},
        {"expected_revision": -1},
        {"node_id": ""},
        {"actor_id": "spoof"},
    ],
)
def test_cancel_payload_remains_strict(cancellation, change):
    client, _, _, runtime, session = cancellation
    assert client.post(commands(session), json=dict(CANCEL, **change)).status_code == 422
    assert runtime.calls == [] and runtime.commands == []
