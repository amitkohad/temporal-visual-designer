"""Signed breakpoint overlays persist without releasing the current pause."""

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


async def test_signed_breakpoint_replace_clear_restart_and_replay(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="breakpoints", correlation_id="breakpoints"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "breakpoint-dispatch.db")
    database = tmp_path / "breakpoint-sessions.db"
    sessions = SQLiteDebugSessionRepository(database)

    async def clients(requested):
        assert requested == placement
        return temporal

    configured = replace(
        backend, debug_sessions=sessions, debug_runtime=TemporalDebugRuntime(clients, journal)
    )
    headers = {"authorization": "Bearer " + token()}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers=headers,
        ) as client:
            created = await client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": timer_project(0)},
            )
            assert created.status_code == 201, created.text
            root = BASE + "/projects/hr/debug-sessions"
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                response = await client.post(root, json=request())
                assert response.status_code == 202, response.text
                session = response.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                original = await wait_state(handle, "paused", "first")
                path = root + "/" + session["session_id"]
                bp_path = path + "/breakpoints"
                body = {
                    "command_id": "set-next",
                    "node_id": "first",
                    "breakpoints": ["second"],
                    "expected_revision": 0,
                }
                changed = await client.post(bp_path, json=body)
                assert changed.status_code == 200, changed.text
                receipt = changed.json()
                paused = await handle.query(ProofWorkflow.snapshot)
                assert paused.state == "paused" and paused.node_id == "first"
                assert paused.completed_nodes == original.completed_nodes
                assert paused.revision == 1
                read = await client.get(bp_path)
                assert read.status_code == 200, read.text
                assert read.headers["cache-control"] == "no-store"
                assert read.json() == {
                    "session_id": session["session_id"],
                    "revision": 1,
                    "breakpoints": ["second"],
                }
                command = await client.post(
                    path + "/commands",
                    json={
                        "command_id": "continue-first",
                        "node_id": "first",
                        "action": "continue",
                        "expected_revision": 1,
                    },
                )
                assert command.status_code == 200, command.text
                await wait_state(handle, "paused", "second")
        sessions.close()
        sessions = SQLiteDebugSessionRepository(database)
        configured = replace(configured, debug_sessions=sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers=headers,
        ) as client:
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                saved = await wait_state(handle, "paused", "second")
                assert saved.revision == 2
                assert (await client.post(bp_path, json=body)).json() == receipt
                assert await handle.query(ProofWorkflow.snapshot) == saved
                cleared = await client.post(
                    bp_path,
                    json={
                        "command_id": "clear",
                        "node_id": "second",
                        "breakpoints": [],
                        "expected_revision": 2,
                    },
                )
                assert cleared.status_code == 200, cleared.text
                after = await handle.query(ProofWorkflow.snapshot)
                assert after.state == "paused" and after.node_id == "second" and after.revision == 3
                assert (await client.get(bp_path)).json()["breakpoints"] == []
                done = await client.post(
                    path + "/commands",
                    json={
                        "command_id": "finish",
                        "node_id": "second",
                        "action": "continue",
                        "expected_revision": 3,
                    },
                )
                assert done.status_code == 200, done.text
                await asyncio.wait_for(handle.result(), timeout=20)
                assert (await client.post(bp_path, json=body)).json() == receipt
                identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
                assert (await client.get(bp_path)).status_code == 403
                assert (await client.post(bp_path, json=body)).status_code == 403
                assert backend.activations.current(scope, "hr") is None
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
    finally:
        sessions.close()
        journal.close()
