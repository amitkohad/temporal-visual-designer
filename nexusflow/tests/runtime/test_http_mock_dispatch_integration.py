"""Opt-in actual Temporal mock dispatch, restart/replay and retry identity acceptance."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import TypeAdapter
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.worker import Replayer, Worker

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.execution import HTTPAttemptExecutor
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_scenarios import StaticHTTPMockScenarios, scenario_digest
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.resolver import AsyncIPv4Resolver
from nexusflow.modules.connectors.temporal_mocks import HTTPJSONMockActivities
from nexusflow.modules.connectors.transport import PinnedHTTPTransport
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.durable_runtime.preflight import compile_plan
from nexusflow.modules.durable_runtime.types import ActivityCall, DebugCommand, ReleaseBinding
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.tests.runtime.test_temporal_integration import temporal_env, wait_state  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-history-private-provider"


@pytest.fixture(autouse=True)
def forbid_live_connector_ports(monkeypatch):
    sentinels = []
    for owner, name in (
        (HTTPAttemptExecutor, "execute"),
        (PinnedHTTPTransport, "send"),
        (AsyncIPv4Resolver, "resolve_ipv4"),
    ):
        sentinel = AsyncMock(side_effect=AssertionError("Mock dispatch must never use live ports"))
        monkeypatch.setattr(owner, name, sentinel)
        sentinels.append(sentinel)
    yield
    for sentinel in sentinels:
        sentinel.assert_not_called()


class WorkerAuthorization:
    def __init__(self):
        self.revoked = False
        self.calls = []

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert scope == SCOPE and permission == "worker.execute" and connection_id == "hr"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "worker-1"


class RecordingAdapter:
    def __init__(self, adapter):
        self.adapter = adapter
        self.outputs = []

    async def execute(self, scope, operation_id, inputs):
        result = await self.adapter.execute(scope, operation_id, inputs)
        self.outputs.append(result)
        return result


def components(*, scenarios=None):
    payload = {"employee": "example", "department": "Engineering"}
    scenario = HTTPMockScenario(
        SCOPE,
        "hr",
        "fixture-v1",
        "/employees",
        HTTPAttemptRequest(method="POST", body=canonical(payload).encode()),
        201,
        (("x-provider-private", PRIVATE),),
        PRIVATE.encode(),
    )
    worker = WorkerAuthorization()
    fixtures = StaticHTTPMockScenarios((scenario,)) if scenarios is None else scenarios
    adapter = RecordingAdapter(
        HTTPJSONActivityAdapter(
            mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(worker, (SCOPE,)), fixtures)
        )
    )
    inputs = {
        "connection_id": "hr",
        "connection_revision": "fixture-v1",
        "execution_mode": "mock",
        "fixture_digest": scenario_digest(scenario),
        "method": "POST",
        "path": "/employees",
        "json_body": payload,
    }
    return worker, adapter, inputs


def plan(inputs, *, debug=False, attempts=1, reference=None):
    reference = reference or http_json_activity_descriptor().activity_ref.model_dump(mode="json")
    definition = {
        "schema_version": "0.1",
        "workflow_id": "employee-onboarding-http",
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
        ReleaseBinding("acme", "dev", "http-release", "sha256:" + "a" * 64, "config-1"),
        SCOPE,
        debug=debug,
    )


def activities(client, queue, adapter):
    return HTTPJSONMockActivities(adapter, SCOPE, client.namespace, queue)


async def start(client, queue, frozen, *, execution=None):
    return await client.start_workflow(
        ProofWorkflow.run,
        frozen,
        id=execution or "http-mock-" + uuid4().hex,
        task_queue=queue,
        execution_timeout=timedelta(minutes=2),
    )


async def test_real_mock_dispatch_survives_debug_worker_restart_and_replay(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "http-mock-" + uuid4().hex
    worker, adapter, inputs = components()
    dispatch = activities(client, queue, adapter)
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        handle = await start(client, queue, plan(inputs, debug=True))
        paused = await wait_state(handle, "paused", "http")
        assert paused.completed_nodes == ("start",) and adapter.outputs == []
    # The workflow's existing durable debug gate survives removing the worker.
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        resumed = await wait_state(handle, "paused", "http")
        assert resumed.revision == 0
        await handle.execute_update(
            ProofWorkflow.control, DebugCommand("continue-http", "http", "continue", 0)
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result.completed_nodes == ("start", "http", "end")
        output = json.loads(result.activity_outputs["http"])
        assert set(output) == {"operation_id", "status_code", "execution_mode"}
        assert output["status_code"] == 201 and output["execution_mode"] == "mock"
        TypeAdapter(Identifier).validate_python(output["operation_id"], strict=True)
        assert output["operation_id"].startswith("http-")
        assert len(adapter.outputs) == 1 and len(worker.calls) == 2
        inspection = await handle.query(ProofWorkflow.step_output, "http")
        assert json.loads(inspection.output_json) == output
    history = await handle.fetch_history()
    assert PRIVATE not in history.to_json()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)


class RetryAfterMockResponse:
    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.operation_ids = []
        self.attempts = []

    @activity.defn(name="nexusflow.proof.dispatch")
    async def run(self, call: ActivityCall) -> str:
        output = await self.dispatch.dispatch(call)
        self.operation_ids.append(json.loads(output)["operation_id"])
        self.attempts.append(activity.info().attempt)
        if activity.info().attempt == 1:
            raise ApplicationError("Synthetic infrastructure failure after mock response")
        return output


async def test_redelivery_identity_stays_stable_and_new_temporal_run_is_distinct(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "http-retry-" + uuid4().hex
    _, adapter, inputs = components()
    retry = RetryAfterMockResponse(activities(client, queue, adapter))
    execution = "http-repeat-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[ProofWorkflow], activities=[retry.run]):
        first = await start(client, queue, plan(inputs, attempts=2), execution=execution)
        await asyncio.wait_for(first.result(), timeout=30)
        assert retry.attempts == [1, 2] and retry.operation_ids[0] == retry.operation_ids[1]
        second = await start(client, queue, plan(inputs, attempts=2), execution=execution)
        await asyncio.wait_for(second.result(), timeout=30)
        assert retry.attempts == [1, 2, 1, 2]
        assert retry.operation_ids[2] == retry.operation_ids[3]
        assert retry.operation_ids[0] != retry.operation_ids[2]
        assert first.first_execution_run_id != second.first_execution_run_id
        assert len(adapter.outputs) == 4
    for handle in (first, second):
        await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


@pytest.mark.parametrize("invalid", ["live", "reference", "revoked"])
async def test_mock_worker_denials_are_private_and_nonretryable(temporal_env, invalid):  # noqa: F811
    client, queue = temporal_env.client, "http-denial-" + uuid4().hex
    worker, adapter, inputs = components()
    reference = None
    if invalid == "live":
        inputs = inputs | {"execution_mode": "live", "fixture_digest": None}
    elif invalid == "reference":
        reference = http_json_activity_descriptor().activity_ref.model_dump(mode="json")
        reference["artifact_digest"] = "sha256:" + "f" * 64
    else:
        worker.revoked = True
    dispatch = activities(client, queue, adapter)
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        handle = await start(client, queue, plan(inputs, attempts=3, reference=reference))
        with pytest.raises(WorkflowFailureError) as caught:
            await asyncio.wait_for(handle.result(), timeout=30)
        cause = caught.value.cause.cause
        assert isinstance(cause, ApplicationError) and cause.non_retryable
        assert cause.type == "HTTPMockDispatch.forbidden"
        assert PRIVATE not in str(caught.value) + str(cause)
        assert adapter.outputs == []
    history = await handle.fetch_history()
    failures = [
        event for event in history.events if event.HasField("activity_task_failed_event_attributes")
    ]
    assert len(failures) == 1 and PRIVATE not in history.to_json()
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(history)


class StalledScenarios:
    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def get(self, *args):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()


async def test_cancelled_workflow_lookup_quiesces_under_mock_deadline(temporal_env):  # noqa: F811
    client, queue = temporal_env.client, "http-cancel-" + uuid4().hex
    stalled = StalledScenarios()
    _, adapter, inputs = components(scenarios=stalled)
    dispatch = activities(client, queue, adapter)
    async with Worker(
        client, task_queue=queue, workflows=[ProofWorkflow], activities=[dispatch.dispatch]
    ):
        handle = await start(client, queue, plan(inputs))
        await asyncio.wait_for(stalled.started.wait(), timeout=20)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError) as caught:
            await asyncio.wait_for(handle.result(), timeout=30)
        assert isinstance(caught.value.cause, CancelledError)
        # No heartbeat protocol is added: workflow cancellation is durable, while
        # the still-running mock lookup may stop under its own bounded deadline.
        # Do not mistake deadline quiescence for immediate worker cancellation.
        await asyncio.wait_for(stalled.stopped.wait(), timeout=20)
        assert adapter.outputs == []
