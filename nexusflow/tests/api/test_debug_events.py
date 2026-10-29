"""Signed metadata event paging, fresh authorization and safe provider failures."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import DebugEventBatch, DebugRuntimeEvent
from nexusflow.tests.api.test_api import api, create, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime


class EventRuntime(DebugRuntime):
    def __init__(self):
        super().__init__()
        self.queries = []
        self.mutation = None
        self.fail = False

    async def events(self, placement, execution_id, run_id, query):
        self.queries.append((placement, execution_id, run_id, query))
        if self.fail:
            raise DebugError("unavailable", "Debug events are unavailable")
        snapshot = await super().snapshot(placement, execution_id, run_id)
        if query.after > 3:
            raise DebugError("invalid_request", "Invalid event cursor")
        events = (
            DebugRuntimeEvent(1, "step_started", "start", "running", 0),
            DebugRuntimeEvent(2, "step_completed", "start", "running", 0),
            DebugRuntimeEvent(3, "paused", "start", "paused", 0),
        )
        selected = events[query.after : query.after + query.limit]
        result = DebugEventBatch(
            snapshot,
            selected,
            1,
            3,
            selected[-1].sequence if selected else query.after,
            query.after + len(selected) < 3,
            False,
        )
        return self.mutation(result) if self.mutation else result


@pytest.fixture
def event_api(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "events.db")
    runtime = EventRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert create(client).status_code == 201
        started = client.post(PATH, json={**PAYLOAD, "input": {"private": "hidden-event-input"}})
        assert started.status_code == 202, started.text
        session = started.json()
        yield client, configured, data, runtime, session
    sessions.close()


def event_path(session):
    return PATH + "/" + session["session_id"] + "/events"


def test_pages_are_ordered_payload_free_and_pin_exact_run(event_api):
    client, backend, data, runtime, session = event_api
    changes = backend.debug_sessions.connection.total_changes
    first = client.get(event_path(session), params={"limit": 2})
    assert first.status_code == 200, first.text
    page = first.json()
    assert [item["sequence"] for item in page["events"]] == [1, 2]
    assert page["next_after"] == 2 and page["has_more"] and not page["gap"]
    assert first.headers["cache-control"] == "no-store"
    second = client.get(event_path(session), params={"after": page["next_after"]})
    assert second.status_code == 200, second.text
    assert [item["sequence"] for item in second.json()["events"]] == [3]
    assert not second.json()["has_more"]
    empty = client.get(event_path(session), params={"after": 3})
    assert empty.json()["events"] == [] and empty.json()["next_after"] == 3
    assert all(
        item[1:3] == (session["execution_id"], session["temporal_run_id"])
        for item in runtime.queries
    )
    assert "hidden-event-input" not in first.text + second.text
    assert all(
        set(item) == {"sequence", "kind", "node_id", "state", "revision"} for item in page["events"]
    )
    assert "snapshot" not in page and "input" not in page and "output" not in page
    assert backend.debug_sessions.connection.total_changes == changes
    assert backend.activations.current(data[1], "hr") is None


@pytest.mark.parametrize(
    "query",
    [
        "after=-1",
        "after=9223372036854775808",
        "after=true",
        "after=x",
        "limit=0",
        "limit=26",
        "after=0&after=1",
        "limit=1&limit=2",
        "actor_id=spoof",
    ],
)
def test_query_bounds_and_ambiguous_parameters_rejected(event_api, query):
    client, _, _, runtime, session = event_api
    response = client.get(event_path(session) + "?" + query)
    assert response.status_code == 422, response.text
    assert runtime.queries == []


def test_owner_and_fresh_revocation_guard(event_api):
    client, _, data, runtime, session = event_api
    client.headers["authorization"] = "Bearer " + data[4](sub="foreign-subject")
    assert client.get(event_path(session)).status_code == 403
    assert runtime.queries == []
    client.headers["authorization"] = "Bearer " + data[4]()
    assert client.get(event_path(session)).status_code == 200
    data[0].revoke_grant(data[1], data[2].grant_id, expected_revision=1)
    assert client.get(event_path(session)).status_code == 403
    assert len(runtime.queries) == 1


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_rejected_before_runtime(event_api, environment):
    client, _, data, runtime, session = event_api
    target = data[1].model_copy(update={"environment_id": environment})
    data[0].save_grant(
        replace(data[2], scope=target, role="organization_administrator"), expected_revision=0
    )
    assert client.get(event_path(session).replace("/dev/", f"/{environment}/")).status_code == 403
    assert runtime.queries == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda result: replace(result, events=tuple(reversed(result.events))),
        lambda result: replace(result, snapshot=replace(result.snapshot, release_id="foreign")),
        lambda result: replace(
            result, events=(replace(result.events[0], node_id="foreign"), *result.events[1:])
        ),
        lambda result: replace(result, gap=True),
    ],
)
def test_inconsistent_provider_pages_fail_safely(event_api, mutation):
    client, _, _, runtime, session = event_api
    runtime.mutation = mutation
    response = client.get(event_path(session))
    assert response.status_code == 503, response.text
    assert "hidden-event-input" not in response.text and len(response.content) < 1024


def test_future_cursor_and_legacy_provider_fail_safely(event_api):
    client, _, _, runtime, session = event_api
    assert client.get(event_path(session), params={"after": 4}).status_code == 422
    runtime.events = None
    assert client.get(event_path(session)).status_code == 503


def test_pending_dispatch_rejected_before_events(event_api, monkeypatch):
    client, backend, data, runtime, _ = event_api

    async def unavailable(*args):
        raise DebugError("unavailable", "Start unavailable")

    monkeypatch.setattr(runtime, "start", unavailable)
    assert client.post(PATH, json={**PAYLOAD, "operation_id": "pending"}).status_code == 503
    record = backend.debug_sessions.operation(data[1], "hr", "pending")
    assert client.get(event_path(record.session.model_dump(mode="json"))).status_code == 409
    assert runtime.queries == []
