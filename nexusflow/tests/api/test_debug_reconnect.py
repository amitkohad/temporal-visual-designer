"""Signed atomic reconnect reads use only the exact-run combined runtime port."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import DebugReconnectState
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime
from nexusflow.tests.runtime.test_debug_session_integration import timer_project


class ReconnectRuntime(DebugRuntime):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.mutation = None

    async def reconnect(self, placement, execution_id, run_id):
        self.reads.append((placement, execution_id, run_id))
        snapshot = await super().snapshot(placement, execution_id, run_id)
        snapshot = replace(snapshot, node_id="first", completed_nodes=("start",))
        result = DebugReconnectState(snapshot, (), 1, 3)
        return self.mutation(result) if self.mutation else result

    async def snapshot(self, *args):
        raise AssertionError("Reconnect must not compose separate status queries")

    async def events(self, *args):
        raise AssertionError("Reconnect must not compose separate event queries")

    async def breakpoints(self, *args):
        raise AssertionError("Reconnect must not compose separate breakpoint queries")

    async def control(self, *args):
        raise AssertionError("Reconnect must not advance workflow")


@pytest.fixture
def reconnect_api(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "reconnect.db")
    runtime = ReconnectRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert (
            client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": timer_project(0)},
            ).status_code
            == 201
        )
        response = client.post(
            PATH, json={**PAYLOAD, "input": {"private": "never-reconnect-secret"}}
        )
        assert response.status_code == 202, response.text
        yield client, configured, data, runtime, response.json()
    sessions.close()


def reconnect_path(session):
    return PATH + "/" + session["session_id"] + "/reconnect"


def test_atomic_snapshot_is_payload_free_no_store_and_single_exact_run_call(reconnect_api):
    client, backend, data, runtime, session = reconnect_api
    changes = backend.debug_sessions.connection.total_changes
    response = client.get(reconnect_path(session))
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {
        "session_id",
        "snapshot",
        "breakpoints",
        "earliest_sequence",
        "latest_sequence",
        "next_after",
    }
    assert result["next_after"] == result["latest_sequence"] == 3
    assert result["snapshot"]["state"] == "paused" and result["snapshot"]["revision"] == 0
    assert result["breakpoints"] == []
    assert "never-reconnect-secret" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(response.content) <= 32768
    assert len(runtime.reads) == 1
    assert runtime.reads[0][1:] == (session["execution_id"], session["temporal_run_id"])
    assert backend.debug_sessions.connection.total_changes == changes
    assert backend.activations.current(data[1], "hr") is None


@pytest.mark.parametrize("query", ["after=0", "limit=1", "after=0&after=1", "actor_id=spoof"])
def test_query_parameters_are_rejected_before_runtime(reconnect_api, query):
    client, _, _, runtime, session = reconnect_api
    response = client.get(reconnect_path(session) + "?" + query)
    assert response.status_code == 422, response.text
    assert runtime.reads == []


def test_owner_and_revocation_are_fresh(reconnect_api):
    client, _, data, runtime, session = reconnect_api
    client.headers["authorization"] = "Bearer " + data[4](sub="foreign-subject")
    assert client.get(reconnect_path(session)).status_code == 403
    assert runtime.reads == []
    client.headers["authorization"] = "Bearer " + data[4]()
    assert client.get(reconnect_path(session)).status_code == 200
    data[0].revoke_grant(data[1], data[2].grant_id, expected_revision=1)
    assert client.get(reconnect_path(session)).status_code == 403
    assert len(runtime.reads) == 1


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_fails_before_runtime(reconnect_api, environment):
    client, _, data, runtime, session = reconnect_api
    scope = data[1].model_copy(update={"environment_id": environment})
    data[0].save_grant(
        replace(data[2], scope=scope, role="organization_administrator"), expected_revision=0
    )
    response = client.get(reconnect_path(session).replace("/dev/", f"/{environment}/"))
    assert response.status_code == 403
    assert runtime.reads == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: replace(state, snapshot=replace(state.snapshot, release_id="foreign")),
        lambda state: replace(state, snapshot=replace(state.snapshot, node_id="unknown")),
        lambda state: replace(state, snapshot=replace(state.snapshot, completed_nodes=("finish",))),
        lambda state: replace(state, snapshot=replace(state.snapshot, state="private-message")),
        lambda state: replace(state, breakpoints=("start",)),
        lambda state: replace(state, latest_sequence=True),
        lambda state: replace(state, earliest_sequence=2),
    ],
)
def test_inconsistent_runtime_metadata_fails_safely(reconnect_api, mutation):
    client, _, _, runtime, session = reconnect_api
    runtime.mutation = mutation
    response = client.get(reconnect_path(session))
    assert response.status_code == 503, response.text
    assert "never-reconnect-secret" not in response.text and "private-message" not in response.text
    assert len(response.content) < 1024


def test_legacy_provider_and_pending_dispatch_fail_safely(reconnect_api, monkeypatch):
    client, backend, data, runtime, session = reconnect_api
    runtime.reconnect = None
    assert client.get(reconnect_path(session)).status_code == 503

    async def unavailable(*args):
        raise DebugError("unavailable", "Start unavailable")

    monkeypatch.setattr(runtime, "start", unavailable)
    assert client.post(PATH, json={**PAYLOAD, "operation_id": "pending"}).status_code == 503
    pending = backend.debug_sessions.operation(data[1], "hr", "pending")
    assert client.get(reconnect_path(pending.session.model_dump(mode="json"))).status_code == 409
    assert runtime.reads == []


def test_large_completed_graph_and_breakpoint_set_fit_metadata_budget(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    project = timer_project(0)
    workflow = project["workflows"][0]
    first, last = workflow["nodes"][0], workflow["nodes"][-1]
    names = [f"wait-{index:03}-" + "x" * 119 for index in range(98)]
    assert all(len(name) == 128 for name in names)
    workflow["nodes"] = [
        first,
        *[
            {
                "node_id": name,
                "kind": "wait",
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
                "input_mapping": {"seconds": 0},
            }
            for name in names
        ],
        last,
    ]
    ordered = [node["node_id"] for node in workflow["nodes"]]
    workflow["edges"] = [
        {
            "source_node_id": source,
            "source_port": "out",
            "target_node_id": target,
            "target_port": "in",
        }
        for source, target in zip(ordered, ordered[1:], strict=False)
    ]

    class LargeRuntime(ReconnectRuntime):
        async def reconnect(self, placement, execution_id, run_id):
            self.reads.append((placement, execution_id, run_id))
            snapshot = await DebugRuntime.snapshot(self, placement, execution_id, run_id)
            snapshot = replace(
                snapshot,
                node_id=ordered[-1],
                state="completed",
                revision=98,
                completed_nodes=tuple(ordered),
            )
            return DebugReconnectState(snapshot, tuple(sorted(names)), 245, 500)

    sessions = SQLiteDebugSessionRepository(tmp_path / "large-reconnect.db")
    runtime = LargeRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    try:
        with TestClient(create_app(configured), raise_server_exceptions=False) as client:
            client.headers.update(original.headers)
            created = client.post(
                BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project}
            )
            assert created.status_code == 201, created.text
            started = client.post(PATH, json={**PAYLOAD, "breakpoints": names})
            assert started.status_code == 202, started.text
            response = client.get(reconnect_path(started.json()))
            assert response.status_code == 200, response.text
            assert 16384 < len(response.content) <= 32768
            result = response.json()
            assert len(result["snapshot"]["completed_nodes"]) == 100
            assert len(result["breakpoints"]) == 98
            assert result["next_after"] == result["latest_sequence"] == 500
            assert len(runtime.reads) == 1
            assert backend.activations.current(data[1], "hr") is None
    finally:
        sessions.close()
