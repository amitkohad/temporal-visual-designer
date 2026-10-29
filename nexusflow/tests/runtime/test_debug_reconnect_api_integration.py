"""Signed atomic reconnect baselines resume exact event progress without races."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
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


async def test_signed_reconnect_baseline_and_later_events_survive_restart(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="reconnect", correlation_id="reconnect"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "reconnect-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "reconnect-sessions.db")

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
                    root, json=request(input={"private": "never-baseline-secret"})
                )
                assert started.status_code == 202, started.text
                session = started.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                await wait_state(handle, "paused", "first")
                path = root + "/" + session["session_id"]
                response = await client.get(path + "/reconnect")
                assert response.status_code == 200, response.text
                baseline = response.json()
                assert response.headers["cache-control"] == "no-store"
                assert baseline["snapshot"]["node_id"] == "first"
                assert baseline["snapshot"]["completed_nodes"] == ["start"]
                assert baseline["snapshot"]["revision"] == 0
                assert baseline["next_after"] == baseline["latest_sequence"] == 3
                assert baseline["breakpoints"] == []
                assert "never-baseline-secret" not in response.text

                changed = await client.post(
                    path + "/breakpoints",
                    json={
                        "command_id": "set-second",
                        "node_id": "first",
                        "breakpoints": ["second"],
                        "expected_revision": 0,
                    },
                )
                assert changed.status_code == 200, changed.text
                current = await client.get(path + "/reconnect")
                assert current.status_code == 200, current.text
                assert current.json()["snapshot"]["revision"] == 1
                assert current.json()["breakpoints"] == ["second"]
                assert current.json()["next_after"] > baseline["next_after"]

                stepped = await client.post(
                    path + "/commands",
                    json={
                        "command_id": "next",
                        "node_id": "first",
                        "action": "continue",
                        "expected_revision": 1,
                    },
                )
                assert stepped.status_code == 200, stepped.text
                paused = await wait_state(handle, "paused", "second")
                events = await client.get(
                    path + "/events", params={"after": baseline["next_after"]}
                )
                assert events.status_code == 200, events.text
                replay = events.json()
                assert not replay["gap"] and not replay["has_more"]
                assert [event["sequence"] for event in replay["events"]] == list(
                    range(baseline["next_after"] + 1, replay["latest_sequence"] + 1)
                )
                assert replay["events"][0]["kind"] == "command_accepted"
                assert replay["events"][-1]["kind"] == "paused"
                assert replay["events"][-1]["node_id"] == "second"
                assert (
                    baseline["snapshot"]["node_id"] == "first"
                    and baseline["snapshot"]["revision"] == 0
                )
                second_response = await client.get(path + "/reconnect")
                assert second_response.status_code == 200, second_response.text
                second = second_response.json()
                assert second["snapshot"]["revision"] == 2
                assert second["next_after"] == replay["latest_sequence"]
                assert second["breakpoints"] == ["second"]
                assert await handle.query(ProofWorkflow.snapshot) == paused

            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                assert await wait_state(handle, "paused", "second") == paused
                restored = await client.get(path + "/reconnect")
                assert restored.status_code == 200, restored.text
                assert restored.json() == second
                finished = await client.post(
                    path + "/commands",
                    json={
                        "command_id": "finish",
                        "node_id": "second",
                        "action": "continue",
                        "expected_revision": 2,
                    },
                )
                assert finished.status_code == 200, finished.text
                await asyncio.wait_for(handle.result(), timeout=20)
                terminal = await client.get(path + "/reconnect")
                assert terminal.status_code == 200, terminal.text
                result = terminal.json()
                assert result["snapshot"]["state"] == "completed"
                assert result["snapshot"]["completed_nodes"] == ["start", "first", "second", "end"]
                assert result["next_after"] == result["latest_sequence"] > second["next_after"]
                tail = await client.get(path + "/events", params={"after": result["next_after"]})
                assert tail.status_code == 200 and tail.json()["events"] == []
                assert "never-baseline-secret" not in terminal.text
                identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
                assert (await client.get(path + "/reconnect")).status_code == 403
                assert backend.activations.current(scope, "hr") is None
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        sessions.close()
        journal.close()
