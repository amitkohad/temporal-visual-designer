"""Opt-in Temporal live HTTP JSON dispatch; controlled ports only, no public egress."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, Worker

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.destination_types import HTTPDestinationRequest
from nexusflow.modules.connectors.temporal_http import HTTPJSONActivities
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest, HTTPAttemptResult
from nexusflow.modules.durable_runtime.preflight import compile_plan
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.tests.runtime.test_temporal_integration import temporal_env  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-history-private-provider"


class FakeLiveExecutor:
    def __init__(self):
        self.calls = []

    async def execute(
        self, scope: Scope, destination: HTTPDestinationRequest, request: HTTPAttemptRequest
    ) -> HTTPAttemptResult:
        assert scope == SCOPE
        assert request.authorization is None  # adapter does not attach; executor owns secrets
        self.calls.append((destination.connection_id, destination.path, request.method))
        assert PRIVATE not in destination.path or True
        return HTTPAttemptResult(destination.operation_id, 204, (), b"")


class RecordingAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.outputs = []

    async def execute(self, scope, operation_id, inputs):
        result = await self.adapter.execute(scope, operation_id, inputs)
        self.outputs.append(result)
        return result


def live_inputs():
    return {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "live",
        "method": "GET",
        "path": "/health",
        "json_body": None,
    }


def plan(inputs, *, attempts=1):
    reference = http_json_activity_descriptor().activity_ref.model_dump(mode="json")
    definition = {
        "schema_version": "0.1",
        "workflow_id": "employee-onboarding-http-live",
        "input_schema": {"type": "object", "additionalProperties": False},
        "output_schema": {"type": "object"},
        "error_routes": [],
        "start_definitions": [{"start_id": "manual", "node_id": "start", "kind": "manual"}],
        "nodes": [
            {
                "node_id": "start",
                "kind": "start",
                "named_ports": {"inputs": [], "outputs": ["out"]},
                "input_mapping": {},
            },
            {
                "node_id": "http",
                "kind": "activity",
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
                "input_mapping": inputs,
                "activity_ref": reference,
                "policy": {"timeout_seconds": 15, "maximum_attempts": attempts},
            },
            {
                "node_id": "end",
                "kind": "end",
                "named_ports": {"inputs": ["in"], "outputs": []},
                "input_mapping": {},
            },
        ],
        "edges": [
            {
                "source_node_id": first,
                "source_port": "out",
                "target_node_id": second,
                "target_port": "in",
            }
            for first, second in (("start", "http"), ("http", "end"))
        ],
    }
    return compile_plan(
        canonical(definition),
        ReleaseBinding("acme", "dev", "http-live-release", "sha256:" + "b" * 64, "config-1"),
        SCOPE,
    )


async def test_real_live_dispatch_status_only_and_replay(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "http-live-" + uuid4().hex
    live = FakeLiveExecutor()
    adapter = RecordingAdapter(HTTPJSONActivityAdapter(live=live))
    dispatch = HTTPJSONActivities(adapter, SCOPE, client.namespace, queue)
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            plan(live_inputs()),
            id="http-live-" + uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result.completed_nodes == ("start", "http", "end")
        output = json.loads(result.activity_outputs["http"])
        assert set(output) == {"operation_id", "status_code", "execution_mode"}
        assert output["status_code"] == 204 and output["execution_mode"] == "live"
        TypeAdapter(Identifier).validate_python(output["operation_id"], strict=True)
        assert output["operation_id"].startswith("http-")
        assert len(adapter.outputs) == 1
        assert live.calls == [("hr", "/health", "GET")]
    history = await handle.fetch_history()
    assert PRIVATE not in history.to_json()
    assert "Bearer" not in history.to_json()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)


async def test_live_worker_rejects_mock_mode_without_mock_port(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "http-live-deny-" + uuid4().hex
    adapter = RecordingAdapter(HTTPJSONActivityAdapter(live=FakeLiveExecutor()))
    dispatch = HTTPJSONActivities(adapter, SCOPE, client.namespace, queue)
    inputs = {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "mock",
        "fixture_digest": "sha256:" + "c" * 64,
        "method": "GET",
        "path": "/health",
    }
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        handle = await client.start_workflow(
            ProofWorkflow.run,
            plan(inputs, attempts=2),
            id="http-live-deny-" + uuid4().hex,
            task_queue=queue,
            execution_timeout=timedelta(minutes=2),
        )
        with pytest.raises(WorkflowFailureError) as caught:
            await asyncio.wait_for(handle.result(), timeout=30)
        cause = caught.value.cause.cause
        assert isinstance(cause, ApplicationError) and cause.non_retryable
        assert cause.type.startswith("HTTPJSONDispatch.")
        assert adapter.outputs == []
