"""Inspect a pinned Dev run with actual Temporal while paused, offline and closed."""

# ruff: noqa: F811
import asyncio
import copy
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
from nexusflow.tests.api.test_debug_inspection_acceptance import (
    INPUT_SCHEMA,
    INPUTS,
    assert_redacted,
    project,
)
from nexusflow.tests.api.test_debug_session_acceptance import request
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_input_inspection_stays_redacted_and_pinned_without_advancing_runtime(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="inspect", correlation_id="inspect"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "inspect-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "inspect-sessions.db")

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
                BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project()}
            )
            assert created.status_code == 201, created.text
            path = BASE + "/projects/hr/debug-sessions"
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                accepted = await client.post(path, json=request(input=INPUTS))
                assert accepted.status_code == 202, accepted.text
                session = accepted.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                paused = await wait_state(handle, "paused", "first")
                inputs_path = path + "/" + session["session_id"] + "/inputs"
                initial = assert_redacted(await client.get(inputs_path))
                assert initial["definition_digest"] == session["definition_digest"]
                unchanged = await handle.query(ProofWorkflow.snapshot)
                assert unchanged == paused
                changed_schema = copy.deepcopy(INPUT_SCHEMA)
                changed_schema["properties"]["unclassified"]["x-nexusflow-inspect"] = True
                saved = await client.put(
                    BASE + "/projects/hr",
                    json={
                        "expected_revision": 1,
                        "summary": "Different public fields",
                        "content": project(changed_schema),
                    },
                )
                assert saved.status_code == 200, saved.text
                assert assert_redacted(await client.get(inputs_path)) == initial
            # No worker is polling. Inspection uses the original saved plan/schema,
            # and never requires a query/update or current draft policy.
            assert assert_redacted(await client.get(inputs_path)) == initial
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                assert (await wait_state(handle, "paused", "first")) == paused
                continued = await client.post(
                    path + "/" + session["session_id"] + "/commands",
                    json={
                        "command_id": "finish-inspection",
                        "node_id": "first",
                        "action": "continue",
                        "expected_revision": 0,
                    },
                )
                assert continued.status_code == 200, continued.text
                result = await asyncio.wait_for(handle.result(), timeout=20)
                assert result["completed_nodes"] == ["start", "first", "second", "end"]
                assert assert_redacted(await client.get(inputs_path)) == initial
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
            assert backend.activations.current(scope, "hr") is None
    finally:
        sessions.close()
        journal.close()
