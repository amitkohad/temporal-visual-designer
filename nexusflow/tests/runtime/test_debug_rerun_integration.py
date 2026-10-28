"""Signed reruns create independent pinned Temporal executions, with durable lineage."""

import asyncio
import json
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


async def test_rerun_edited_revision_is_independent_and_survives_provider_restart(
    api,  # noqa: F811
    temporal_env,  # noqa: F811
    tmp_path,
):
    _, backend, data, _ = api
    identity_store, scope, grant, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="rerun", correlation_id="rerun"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "dispatch.db")
    database = tmp_path / "sessions.db"
    sessions = SQLiteDebugSessionRepository(database)

    async def clients(requested):
        assert requested == placement
        return temporal

    runtime = TemporalDebugRuntime(clients, journal)
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    path = BASE + "/projects/hr/debug-sessions"
    headers = {"authorization": "Bearer " + token()}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers=headers,
        ) as client:
            created = await client.post(
                BASE + "/projects",
                json={"project_id": "hr", "name": "HR", "content": timer_project(60)},
            )
            assert created.status_code == 201, created.text
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                response = await client.post(
                    path, json=request(input={"parent-private": "never-copy-this"})
                )
                assert response.status_code == 202, response.text
                parent = response.json()
                original = temporal.get_workflow_handle(
                    parent["execution_id"], run_id=parent["temporal_run_id"]
                )
                original_snapshot = await wait_state(original, "paused", "first")
                saved = await client.put(
                    BASE + "/projects/hr",
                    json={
                        "expected_revision": 1,
                        "summary": "Rerun the edited draft",
                        "content": timer_project(0),
                    },
                )
                assert saved.status_code == 200, saved.text

            # Rerun requires no query or control of the original execution: its
            # worker is stopped, but a new pinned start can still be acknowledged.
            rerun_path = path + "/" + parent["session_id"] + "/reruns"
            payload = request(operation_id="rerun-1", source_revision=2, input={})
            response = await client.post(rerun_path, json=payload)
            assert response.status_code == 202, response.text
            assert response.headers["cache-control"] == "no-store"
            child = response.json()
            assert child["session_id"] != parent["session_id"]
            assert child["execution_id"] != parent["execution_id"]
            assert child["temporal_run_id"] != parent["temporal_run_id"]
            assert child["definition_digest"] != parent["definition_digest"]
            assert child["source_revision"] == 2 and parent["source_revision"] == 1
            assert "never-copy-this" not in response.text
            assert json.loads(sessions.get(scope, "hr", child["session_id"]).plan.input_json) == {}
            assert json.loads(sessions.get(scope, "hr", parent["session_id"]).plan.input_json) == {
                "parent-private": "never-copy-this"
            }
            assert (await client.post(rerun_path, json=payload)).json() == child
            assert backend.activations.current(scope, "hr") is None

        sessions.close()
        sessions = SQLiteDebugSessionRepository(database)
        configured = replace(configured, debug_sessions=sessions)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(configured)),
            base_url="http://local-api",
            headers=headers,
        ) as client:
            assert (await client.post(rerun_path, json=payload)).json() == child
            # A normal create cannot adopt a rerun operation after recovery.
            assert (await client.post(path, json=payload)).status_code == 409
            changed = await client.post(rerun_path, json={**payload, "input": {"changed": True}})
            assert changed.status_code == 409, changed.text
            rerun = temporal.get_workflow_handle(
                child["execution_id"], run_id=child["temporal_run_id"]
            )
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                assert await wait_state(original, "paused", "first") == original_snapshot
                child_snapshot = await wait_state(rerun, "paused", "first")
                assert child_snapshot.definition_digest == child["definition_digest"]
                done = await client.post(
                    path + "/" + child["session_id"] + "/commands",
                    json={
                        "command_id": "finish-child",
                        "node_id": "first",
                        "action": "continue",
                        "expected_revision": 0,
                    },
                )
                assert done.status_code == 200, done.text
                result = await asyncio.wait_for(rerun.result(), timeout=20)
                assert result["completed_nodes"] == ["start", "first", "second", "end"]
                assert await original.query(ProofWorkflow.snapshot) == original_snapshot
                cancelled = await client.post(
                    path + "/" + parent["session_id"] + "/commands",
                    json={
                        "command_id": "cancel-parent",
                        "node_id": "first",
                        "action": "cancel",
                        "expected_revision": 0,
                    },
                )
                assert cancelled.status_code == 200, cancelled.text
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(original.result(), timeout=20)

            assert (await client.post(rerun_path, json=payload)).json() == child
            identity_store.revoke_grant(scope, grant.grant_id, expected_revision=1)
            assert (await client.post(rerun_path, json=payload)).status_code == 403
            assert backend.activations.current(scope, "hr") is None
            for handle in (original, rerun):
                await Replayer(workflows=[ProofWorkflow]).replay_workflow(
                    await handle.fetch_history()
                )
    finally:
        sessions.close()
        journal.close()
