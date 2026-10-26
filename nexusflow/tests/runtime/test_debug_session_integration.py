"""Signed draft-debug API against an actual Temporal namespace and worker replay."""

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
from nexusflow.tests.package_promotion.test_activation import content
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def timer_project(seconds=2):
    project = content()
    for workflow in project["workflows"]:
        nodes = workflow["nodes"]
        middle = [
            {
                "node_id": name,
                "kind": "wait",
                "input_mapping": {"seconds": duration},
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
            }
            for name, duration in (("first", seconds), ("second", 0))
        ]
        workflow["nodes"] = [nodes[0], *middle, nodes[-1]]
        names = [node["node_id"] for node in workflow["nodes"]]
        workflow["edges"] = [
            {
                "source_node_id": source,
                "source_port": "out",
                "target_node_id": target,
                "target_port": "in",
            }
            for source, target in zip(names, names[1:], strict=False)
        ]
    return project


async def test_draft_debug_steps_timer_survives_restart_and_replays(api, temporal_env, tmp_path):  # noqa: F811
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="debug", correlation_id="debug"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "debug-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "debug-sessions.db")

    async def clients(requested):
        assert requested == placement
        return temporal

    configured = replace(
        backend, debug_sessions=sessions, debug_runtime=TemporalDebugRuntime(clients, journal)
    )
    app = create_app(configured)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://local-api",
            headers={"authorization": "Bearer " + token()},
        ) as client:
            created = await client.post(
                BASE + "/projects",
                json={
                    "project_id": "hr",
                    "name": "HR",
                    "content": timer_project(),
                },
            )
            assert created.status_code == 201, created.text
            path = BASE + "/projects/hr/debug-sessions"
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                response = await client.post(path, json=request())
                assert response.status_code == 202, response.text
                session = response.json()
                assert backend.activations.current(scope, "hr") is None
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                await wait_state(handle, "paused", "first")
                status = await client.get(path + "/" + session["session_id"])
                assert status.status_code == 200, status.text
                assert status.json()["snapshot"]["state"] == "paused"
                command = {
                    "command_id": "step-first",
                    "node_id": "first",
                    "action": "step_over",
                    "expected_revision": 0,
                }
                command_path = path + "/" + session["session_id"] + "/commands"
                receipt = await client.post(command_path, json=command)
                assert receipt.status_code == 200, receipt.text
                waiting = await wait_state(handle, "waiting_timer", "first")
                assert "first" not in waiting.completed_nodes
                assert (await client.post(command_path, json=command)).json() == receipt.json()
                premature = await client.post(
                    command_path,
                    json={
                        "command_id": "premature",
                        "node_id": "second",
                        "action": "continue",
                        "expected_revision": receipt.json()["revision"],
                    },
                )
                assert premature.status_code == 409, premature.text
                await wait_state(handle, "paused", "second")
                saved = await client.put(
                    BASE + "/projects/hr",
                    json={
                        "expected_revision": 1,
                        "summary": "Second draft",
                        "content": timer_project(0),
                    },
                )
                assert saved.status_code == 200, saved.text
                assert (await client.post(path, json=request())).json() == session
            # The original debug snapshot stays paused across worker replacement.
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                paused = await wait_state(handle, "paused", "second")
                assert paused.definition_digest == session["definition_digest"]
                continuation = await client.post(
                    command_path,
                    json={
                        "command_id": "finish",
                        "node_id": "second",
                        "action": "continue",
                        "expected_revision": receipt.json()["revision"],
                    },
                )
                assert continuation.status_code == 200, continuation.text
                result = await asyncio.wait_for(handle.result(), timeout=20)
                assert result["completed_nodes"] == ["start", "first", "second", "end"]
                next_session = await client.post(
                    path, json=request(operation_id="debug-2", source_revision=2)
                )
                assert next_session.status_code == 202, next_session.text
                assert next_session.json()["execution_id"] != session["execution_id"]
                assert next_session.json()["definition_digest"] != session["definition_digest"]
                next_handle = temporal.get_workflow_handle(
                    next_session.json()["execution_id"],
                    run_id=next_session.json()["temporal_run_id"],
                )
                await wait_state(next_handle, "paused", "first")
                await next_handle.cancel()
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
            assert backend.activations.current(scope, "hr") is None
    finally:
        sessions.close()
        journal.close()
