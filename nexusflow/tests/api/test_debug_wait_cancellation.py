"""Signed wait-cancellation composition; runtime policy uses an explicit fake port."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugControl, DebugError
from nexusflow.modules.durable_runtime.types import CommandReceipt
from nexusflow.tests.api.test_api import api, create, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime

CANCEL = {
    "command_id": "cancel-wait",
    "node_id": "waiting-node",
    "action": "cancel",
    "expected_revision": 3,
}


class WaitingRuntime(DebugRuntime):
    """Trusted waiting-state fixture; actual Temporal semantics are tested separately."""

    def __init__(self):
        super().__init__()
        self.state = "waiting_timer"
        self.managed_approval = False
        self.available = True
        self.commands = []

    async def snapshot(self, placement, execution_id, run_id):
        if not self.available:
            raise DebugError("unavailable", "Waiting runtime is unavailable")
        return replace(
            await super().snapshot(placement, execution_id, run_id),
            node_id="waiting-node",
            revision=3,
            state=self.state,
        )

    async def control(self, placement, execution_id, run_id, command):
        self.commands.append((placement, execution_id, run_id, command))
        if (
            command.action != "cancel"
            or command.node_id != "waiting-node"
            or command.expected_revision != 3
            or self.state not in {"waiting_timer", "waiting_approval"}
            or (self.state == "waiting_approval" and self.managed_approval)
        ):
            raise DebugError("conflict", "Runtime does not permit this cancellation")
        self.state = "cancelled"
        return CommandReceipt(command.command_id, 4)


@pytest.fixture
def waiting(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    repository = SQLiteDebugSessionRepository(tmp_path / "wait-debug.db")
    runtime = WaitingRuntime()
    backend = replace(backend, debug_sessions=repository, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert create(client).status_code == 201
        response = client.post(PATH, json=PAYLOAD)
        assert response.status_code == 202, response.text
        session = response.json()
        path = PATH + "/" + session["session_id"]
        yield client, backend, data, runtime, session, path
    repository.close()


@pytest.mark.parametrize("state", ["waiting_timer", "waiting_approval"])
def test_wait_cancel_uses_current_node_revision_and_pinned_run(waiting, state):
    client, backend, data, runtime, session, path = waiting
    runtime.state = state
    status = client.get(path)
    assert status.status_code == 200, status.text
    assert status.json()["snapshot"]["state"] == state
    response = client.post(path + "/commands", json=CANCEL)
    assert response.status_code == 200, response.text
    assert response.json() == {"command_id": "cancel-wait", "revision": 4}
    placement, execution, run_id, command = runtime.commands[0]
    assert placement.scope == data[1]
    assert (execution, run_id) == (session["execution_id"], session["temporal_run_id"])
    assert (command.node_id, command.expected_revision) == ("waiting-node", 3)
    assert backend.activations.current(data[1], "hr") is None
    runtime.available = False
    assert client.post(path + "/commands", json=CANCEL).json() == response.json()
    assert len(runtime.commands) == 1


@pytest.mark.parametrize("state", ["waiting_timer", "waiting_approval"])
def test_wait_cancel_receipt_survives_reopen_and_still_checks_revocation(waiting, tmp_path, state):
    client, backend, data, runtime, _, path = waiting
    runtime.state = state
    response = client.post(path + "/commands", json=CANCEL)
    assert response.status_code == 200, response.text
    backend.debug_sessions.close()
    runtime.available = False
    repository = SQLiteDebugSessionRepository(tmp_path / "wait-debug.db")
    try:
        with TestClient(
            create_app(replace(backend, debug_sessions=repository)),
            raise_server_exceptions=False,
        ) as restarted:
            restarted.headers.update(client.headers)
            retry = restarted.post(path + "/commands", json=CANCEL)
            assert retry.status_code == 200, retry.text
            assert retry.json() == response.json()
            identity_repository, scope, grant, _, _ = data
            identity_repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
            assert restarted.post(path + "/commands", json=CANCEL).status_code == 403
            assert len(runtime.commands) == 1
    finally:
        repository.close()


def test_managed_wait_rejection_is_propagated_without_success_receipt(waiting):
    client, backend, data, runtime, session, path = waiting
    runtime.state = "waiting_approval"
    runtime.managed_approval = True
    response = client.post(path + "/commands", json=CANCEL)
    assert response.status_code == 409, response.text
    assert runtime.state == "waiting_approval"
    assert (
        backend.debug_sessions.claim_command(
            data[1], "hr", session["session_id"], DebugControl(**CANCEL)
        )
        is None
    )


@pytest.mark.parametrize("change", [{"node_id": "other"}, {"expected_revision": 2}])
def test_wait_cancel_runtime_conflicts_preserve_waiting_state(waiting, change):
    client, _, _, runtime, _, path = waiting
    response = client.post(path + "/commands", json=dict(CANCEL, **change))
    assert response.status_code == 409, response.text
    assert runtime.state == "waiting_timer"


def test_other_owner_cannot_cancel_wait_or_retrieve_saved_receipt(waiting):
    client, _, data, runtime, _, path = waiting
    assert client.post(path + "/commands", json=CANCEL).status_code == 200
    snapshots = len(runtime.calls)
    client.headers["authorization"] = "Bearer " + data[4](sub="other-pairwise-subject")
    assert client.post(path + "/commands", json=CANCEL).status_code == 403
    assert len(runtime.calls) == snapshots
    assert len(runtime.commands) == 1
