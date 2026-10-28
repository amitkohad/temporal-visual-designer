"""Pinned purchase collections stay redacted through actual Temporal execution."""

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
from nexusflow.tests.api.test_debug_session_acceptance import request
from nexusflow.tests.runtime.test_debug_session_integration import timer_project
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PUBLIC_STRING = {"type": "string", "x-nexusflow-inspect": True}
ARRAY_SCHEMA = {
    "type": "object",
    "properties": {
        "legacy_collection": {"type": "array", "x-nexusflow-inspect": True, "items": PUBLIC_STRING},
        "line_items": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "items": {
                "type": "object",
                "properties": {
                    "sku": PUBLIC_STRING,
                    "quantity": {"type": "integer", "x-nexusflow-inspect": True},
                    "vendor_secret": {
                        "type": "string",
                        "writeOnly": True,
                        "x-nexusflow-inspect": True,
                    },
                },
            },
        },
        "labels": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "items": PUBLIC_STRING,
        },
        "batches": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "items": {
                "type": "array",
                "x-nexusflow-inspect": True,
                "x-nexusflow-inspect-array": True,
                "items": {"type": "integer", "x-nexusflow-inspect": True},
            },
        },
        "hidden_collection": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "x-secret": True,
            "items": PUBLIC_STRING,
        },
        "hidden_items": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "items": {"type": "string", "format": "password", "x-nexusflow-inspect": True},
        },
        "tuple_collection": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "prefixItems": [{"type": "string"}],
            "items": PUBLIC_STRING,
        },
        "contains_collection": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "x-nexusflow-inspect-array": True,
            "contains": {"type": "string"},
            "items": PUBLIC_STRING,
        },
    },
}
ARRAY_INPUTS = {
    "legacy_collection": ["legacy-private-array"],
    "line_items": [
        {
            "sku": "public-a",
            "quantity": 2,
            "vendor_secret": "line-secret-a",
            "unknown-secret-key": "unknown-secret-value",
        },
        {"sku": "public-b", "quantity": 1, "vendor_secret": "line-secret-b"},
    ],
    "labels": ["ready", "L" * 1100, "done"],
    "batches": [[1, 2], [3]],
    "hidden_collection": ["parent-private-one", "parent-private-two"],
    "hidden_items": ["item-password-private"],
    "tuple_collection": ["tuple-private"],
    "contains_collection": ["contains-private"],
}


def array_project(schema=None):
    result = timer_project(0)
    for workflow in result["workflows"]:
        workflow["input_schema"] = copy.deepcopy(ARRAY_SCHEMA if schema is None else schema)
    return result


def assert_collection_projection(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"] == {
        "line_items": [{"sku": "public-a", "quantity": 2}, {"sku": "public-b", "quantity": 1}],
        "labels": ["ready", None, "done"],
        "batches": [[1, 2], [3]],
    }
    assert body["truncated"] is True
    assert "/labels/1" in body["redacted_paths"]
    assert "/line_items/0/vendor_secret" in body["redacted_paths"]
    assert "/line_items/0" in body["redacted_paths"]
    for hidden in (
        "legacy_collection",
        "hidden_collection",
        "hidden_items",
        "tuple_collection",
        "contains_collection",
    ):
        assert "/" + hidden in body["redacted_paths"]
        assert not any(path.startswith("/" + hidden + "/") for path in body["redacted_paths"])
    for private in (
        "legacy-private-array",
        "line-secret-a",
        "line-secret-b",
        "unknown-secret-key",
        "unknown-secret-value",
        "parent-private-one",
        "parent-private-two",
        "item-password-private",
        "tuple-private",
        "contains-private",
        "L" * 1100,
    ):
        assert private not in response.text
    assert len(response.content) <= 16384
    return body


async def test_purchase_collection_projection_stays_pinned_paused_offline_and_closed(
    api, temporal_env, tmp_path
):
    _, backend, data, _ = api
    _, scope, _, _, token = data
    placement = backend.identity.placement(
        token(), scope, request_id="inspect-array", correlation_id="inspect-array"
    )
    await temporal_env.client.workflow_service.register_namespace(
        RegisterNamespaceRequest(
            namespace=placement.temporal_namespace,
            workflow_execution_retention_period=Duration(seconds=86400),
        )
    )
    temporal = Client(temporal_env.client.service_client, namespace=placement.temporal_namespace)
    journal = SQLiteDispatchJournal(tmp_path / "array-dispatch.db")
    sessions = SQLiteDebugSessionRepository(tmp_path / "array-sessions.db")

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
                json={"project_id": "purchase", "name": "Purchase", "content": array_project()},
            )
            assert created.status_code == 201, created.text
            path = BASE + "/projects/purchase/debug-sessions"
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                accepted = await client.post(
                    path, json=request(operation_id="array-run", input=ARRAY_INPUTS)
                )
                assert accepted.status_code == 202, accepted.text
                session = accepted.json()
                handle = temporal.get_workflow_handle(
                    session["execution_id"], run_id=session["temporal_run_id"]
                )
                paused = await wait_state(handle, "paused", "first")
                inputs_path = path + "/" + session["session_id"] + "/inputs"
                first = assert_collection_projection(await client.get(inputs_path))
                assert first["definition_digest"] == session["definition_digest"]
                assert (await handle.query(ProofWorkflow.snapshot)) == paused
                changed_schema = copy.deepcopy(ARRAY_SCHEMA)
                changed_schema["properties"]["line_items"]["items"]["properties"]["quantity"][
                    "x-nexusflow-inspect"
                ] = False
                saved = await client.put(
                    BASE + "/projects/purchase",
                    json={
                        "expected_revision": 1,
                        "summary": "New collection policy",
                        "content": array_project(changed_schema),
                    },
                )
                assert saved.status_code == 200, saved.text
                assert assert_collection_projection(await client.get(inputs_path)) == first
            # Inspection does not need a worker or emit a durable control permit.
            assert assert_collection_projection(await client.get(inputs_path)) == first
            async with Worker(temporal, task_queue=placement.task_queue, workflows=[ProofWorkflow]):
                assert (await wait_state(handle, "paused", "first")) == paused
                continued = await client.post(
                    path + "/" + session["session_id"] + "/commands",
                    json={
                        "command_id": "finish-arrays",
                        "node_id": "first",
                        "action": "continue",
                        "expected_revision": 0,
                    },
                )
                assert continued.status_code == 200, continued.text
                result = await asyncio.wait_for(handle.result(), timeout=20)
                assert result["completed_nodes"] == ["start", "first", "second", "end"]
                assert assert_collection_projection(await client.get(inputs_path)) == first
            await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())
            assert backend.activations.current(scope, "purchase") is None
    finally:
        sessions.close()
        journal.close()
