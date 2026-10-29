"""Signed ordered metadata replay survives reconnect and worker replacement."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Replayer, Worker

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_session_acceptance import request
from nexusflow.tests.runtime.test_debug_session_integration import timer_project
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def collect(client, path, after=0):
    events = []
    for _ in range(30):
        response = await client.get(path, params={"after": after, "limit": 2})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        page = response.json()
        assert page["gap"] is False
        events.extend(page["events"])
        after = page["next_after"]
        if not page["has_more"]:
            return events, after
    raise AssertionError("Unexpected unbounded event pages")


async def test_signed_metadata_reconnect_retry_and_restart_replay(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="events", correlation_id="events"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "event-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "event-sessions.db")

    async def clients(requested):
        assert requested == placement
        return temporal

    configured = replace(
        backend, debug_sessions=sessions, debug_runtime=TemporalDebugRuntime(clients, journal)
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers={"authorization": "Bearer " + token()},
        ) as client:
            created = await client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": timer_project(0)},
            )
            assert created.status_code == 201, created.text
            root = BASE + "/projects/hr/debug-sessions"
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                started = await client.post(
                    root, json=request(input={"private": "never-emit-event-secret"})
                )
                assert started.status_code == 202, started.text
                session = started.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                paused = await wait_state(handle, "paused", "first")
                path = root + "/" + session["session_id"]
                events_path = path + "/events"
                initial, cursor = await collect(client, events_path)
                assert [event["sequence"] for event in initial] == list(range(1, cursor + 1))
                assert initial[-1]["kind"] == "paused" and initial[-1]["node_id"] == "first"
                assert all(
                    set(event) == {"sequence", "kind", "node_id", "state", "revision"}
                    for event in initial
                )
                assert "never-emit-event-secret" not in str(initial)
                assert await handle.query(ProofWorkflow.snapshot) == paused
                command = {
                    "command_id": "step",
                    "node_id": "first",
                    "action": "step_over",
                    "expected_revision": 0,
                }
                stepped = await client.post(path + "/commands", json=command)
                assert stepped.status_code == 200, stepped.text
                second = await wait_state(handle, "paused", "second")
                arrived, second_cursor = await collect(client, events_path, cursor)
                assert arrived[0]["sequence"] == cursor + 1
                assert arrived[-1]["kind"] == "paused" and arrived[-1]["node_id"] == "second"
                assert any(event["kind"] == "command_accepted" for event in arrived)
                assert (
                    await client.post(path + "/commands", json=command)
                ).json() == stepped.json()
                assert await collect(client, events_path, cursor) == (arrived, second_cursor)
                assert await handle.query(ProofWorkflow.snapshot) == second
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                assert await wait_state(handle, "paused", "second") == second
                assert await collect(client, events_path, cursor) == (arrived, second_cursor)
                cancelled = await client.post(
                    path + "/commands",
                    json={
                        "command_id": "cancel",
                        "node_id": "second",
                        "action": "cancel",
                        "expected_revision": 1,
                    },
                )
                assert cancelled.status_code == 200, cancelled.text
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), timeout=20)
                final, final_cursor = await collect(client, events_path, second_cursor)
                assert final[-1]["kind"] == "cancelled" and final[-1]["state"] == "cancelled"
                assert final_cursor > second_cursor
                assert "never-emit-event-secret" not in str(initial + arrived + final)
                identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
                assert (await client.get(events_path)).status_code == 403
                assert backend.activations.current(scope, "hr") is None
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        sessions.close()
        journal.close()
