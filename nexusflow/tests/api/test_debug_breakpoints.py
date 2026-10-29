"""Signed breakpoint controls share durable identity with existing controls."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import CommandReceipt, DebugBreakpointState
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime
from nexusflow.tests.runtime.test_debug_session_integration import timer_project

CHANGE = {
    "command_id": "breakpoints-1",
    "node_id": "first",
    "breakpoints": ["second"],
    "expected_revision": 0,
}


class BreakpointRuntime(DebugRuntime):
    def __init__(self):
        super().__init__()
        self.active = ()
        self.revision = 0
        self.state = "paused"
        self.available = True
        self.changes = []
        self.reads = []

    async def snapshot(self, placement, execution_id, run_id):
        if not self.available:
            raise DebugError("unavailable", "Runtime unavailable")
        return replace(
            await super().snapshot(placement, execution_id, run_id),
            node_id="first",
            state=self.state,
            revision=self.revision,
        )

    async def breakpoints(self, placement, execution_id, run_id):
        self.reads.append((placement, execution_id, run_id))
        return DebugBreakpointState(
            await self.snapshot(placement, execution_id, run_id), self.active
        )

    async def replace_breakpoints(self, placement, execution_id, run_id, command):
        if not self.available:
            raise DebugError("unavailable", "Runtime unavailable")
        if (
            self.state != "paused"
            or command.node_id != "first"
            or command.expected_revision != self.revision
        ):
            raise DebugError("conflict", "Not the current pause")
        if any(node not in {"first", "second"} for node in command.breakpoints):
            raise DebugError("conflict", "Unknown target")
        self.changes.append((placement, execution_id, run_id, command))
        self.active = command.breakpoints
        self.revision += 1
        return CommandReceipt(command.command_id, self.revision)


@pytest.fixture
def breakpoints(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "breakpoints.db")
    runtime = BreakpointRuntime()
    backend = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert (
            client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": timer_project(0)},
            ).status_code
            == 201
        )
        response = client.post(PATH, json=PAYLOAD)
        assert response.status_code == 202, response.text
        yield client, backend, data, runtime, response.json()
    sessions.close()


def path(session):
    return PATH + "/" + session["session_id"] + "/breakpoints"


def test_replace_and_read_bind_exact_run_without_release(breakpoints):
    client, backend, data, runtime, session = breakpoints
    response = client.post(path(session), json={**CHANGE, "breakpoints": ["second", "first"]})
    assert response.status_code == 200, response.text
    assert response.json() == {"command_id": "breakpoints-1", "revision": 1}
    read = client.get(path(session))
    assert read.status_code == 200, read.text
    assert read.json() == {
        "session_id": session["session_id"],
        "revision": 1,
        "breakpoints": ["first", "second"],
    }
    assert response.headers["cache-control"] == read.headers["cache-control"] == "no-store"
    placement, execution, run_id, command = runtime.changes[0]
    assert placement.scope == data[1]
    assert (execution, run_id) == (session["execution_id"], session["temporal_run_id"])
    assert command.breakpoints == ("first", "second")
    assert runtime.state == "paused"
    assert backend.activations.current(data[1], "hr") is None


def test_saved_receipt_and_canonical_set_retry_survive_reopen(breakpoints, tmp_path):
    client, backend, _, runtime, session = breakpoints
    body = {**CHANGE, "breakpoints": ["second", "first"]}
    response = client.post(path(session), json=body)
    assert response.status_code == 200, response.text
    backend.debug_sessions.close()
    runtime.available = False
    reopened = SQLiteDebugSessionRepository(tmp_path / "breakpoints.db")
    try:
        with TestClient(
            create_app(replace(backend, debug_sessions=reopened)), raise_server_exceptions=False
        ) as restored:
            restored.headers.update(client.headers)
            retry = restored.post(path(session), json={**body, "breakpoints": ["first", "second"]})
            assert retry.status_code == 200, retry.text
            assert retry.json() == response.json()
            assert len(runtime.changes) == 1
    finally:
        reopened.close()


def test_changed_body_and_cross_kind_command_ids_conflict(breakpoints):
    client, _, _, runtime, session = breakpoints
    assert client.post(path(session), json=CHANGE).status_code == 200
    assert client.post(path(session), json={**CHANGE, "breakpoints": []}).status_code == 409
    controls = PATH + "/" + session["session_id"] + "/commands"
    assert (
        client.post(
            controls,
            json={
                "command_id": CHANGE["command_id"],
                "node_id": "first",
                "action": "continue",
                "expected_revision": 1,
            },
        ).status_code
        == 409
    )
    assert len(runtime.changes) == 1


def test_pending_identity_cannot_change_before_success(breakpoints):
    client, _, _, runtime, session = breakpoints
    runtime.available = False
    assert client.post(path(session), json=CHANGE).status_code == 503
    assert client.post(path(session), json={**CHANGE, "breakpoints": []}).status_code == 409
    runtime.available = True
    assert client.post(path(session), json=CHANGE).status_code == 200
    assert len(runtime.changes) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"breakpoints": ["second", "second"]},
        {"breakpoints": ["bad/id"]},
        {"expected_revision": -1},
        {"actor_id": "spoof"},
    ],
)
def test_strict_requests_fail_before_runtime(breakpoints, change):
    client, _, _, runtime, session = breakpoints
    assert client.post(path(session), json={**CHANGE, **change}).status_code == 422
    assert runtime.changes == [] and runtime.reads == []


@pytest.mark.parametrize("target", ["start", "end", "unknown"])
def test_invalid_plan_targets_cannot_change_breakpoints(breakpoints, target):
    client, _, _, runtime, session = breakpoints
    assert client.post(path(session), json={**CHANGE, "breakpoints": [target]}).status_code == 422
    assert runtime.changes == []


def test_other_owner_and_revocation_block_reads_and_saved_retries(breakpoints):
    client, _, data, runtime, session = breakpoints
    assert client.post(path(session), json=CHANGE).status_code == 200
    client.headers["authorization"] = "Bearer " + data[4](sub="other")
    assert client.get(path(session)).status_code == 403
    assert client.post(path(session), json=CHANGE).status_code == 403
    client.headers["authorization"] = "Bearer " + data[4]()
    data[0].revoke_grant(data[1], data[2].grant_id, expected_revision=1)
    assert client.get(path(session)).status_code == 403
    assert client.post(path(session), json=CHANGE).status_code == 403
    assert len(runtime.changes) == 1 and runtime.reads == []


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_denied_before_runtime(breakpoints, environment):
    client, _, data, runtime, session = breakpoints
    repo, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    repo.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    url = path(session).replace("/dev/", f"/{environment}/")
    assert client.get(url).status_code == 403
    assert client.post(url, json=CHANGE).status_code == 403
    assert runtime.changes == [] and runtime.reads == []


def test_normal_command_id_cannot_be_adopted_as_breakpoint_change(breakpoints):
    client, _, _, runtime, session = breakpoints
    controls = PATH + "/" + session["session_id"] + "/commands"
    response = client.post(
        controls,
        json={
            "command_id": "ordinary",
            "node_id": "first",
            "action": "step_over",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert client.post(path(session), json={**CHANGE, "command_id": "ordinary"}).status_code == 409
    assert runtime.changes == []


@pytest.mark.parametrize("change", [{"node_id": "second"}, {"expected_revision": 1}])
def test_wrong_pause_or_revision_cannot_replace_breakpoints(breakpoints, change):
    client, _, _, runtime, session = breakpoints
    response = client.post(path(session), json={**CHANGE, **change})
    assert response.status_code == 409, response.text
    assert runtime.changes == [] and runtime.active == ()


def test_clear_is_explicit_and_leaves_current_pause_intact(breakpoints):
    client, _, _, runtime, session = breakpoints
    assert client.post(path(session), json=CHANGE).status_code == 200
    cleared = client.post(
        path(session),
        json={**CHANGE, "command_id": "clear", "breakpoints": [], "expected_revision": 1},
    )
    assert cleared.status_code == 200, cleared.text
    assert client.get(path(session)).json()["breakpoints"] == []
    assert runtime.state == "paused" and runtime.revision == 2
