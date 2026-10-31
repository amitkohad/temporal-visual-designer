"""Worker bridge context, privacy and cancellation without a Temporal server."""

import asyncio
import json
import math
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors import temporal_mocks
from nexusflow.modules.connectors.activity_descriptor import http_json_activity_descriptor
from nexusflow.modules.connectors.activity_types import HTTPActivityError, HTTPJSONActivityOutput
from nexusflow.modules.connectors.temporal_mocks import HTTPJSONMockActivities
from nexusflow.modules.durable_runtime.types import ActivityCall, ReleaseBinding

PRIVATE = "private-worker-sentinel"
SCOPE = Scope(organization_id="acme", environment_id="dev")
REF = http_json_activity_descriptor().activity_ref


def call(**changes):
    value = ActivityCall(
        binding=ReleaseBinding("acme", "dev", "release-1", "sha256:" + "b" * 64, "config-1"),
        node_id="http-step",
        operation_id="execution-1:http-step",
        activity_id=REF.activity_id,
        activity_version=REF.version,
        artifact_digest=REF.artifact_digest,
        input_json=json.dumps(
            {
                "connection_id": "hr",
                "connection_revision": "v1",
                "execution_mode": "mock",
                "fixture_digest": "sha256:" + "a" * 64,
                "path": "/employees?private=" + PRIVATE,
            }
        ),
        workflow_input_json="{}",
    )
    return replace(value, **changes)


@pytest.fixture
def worker(monkeypatch):
    info = SimpleNamespace(
        namespace="nexus",
        workflow_namespace="nexus",
        task_queue="worker",
        workflow_type="NexusFlowProofV1",
        workflow_id="execution-1",
        workflow_run_id="temporal-run-1",
        activity_type="nexusflow.proof.dispatch",
        activity_id="http-step",
        attempt=1,
    )
    monkeypatch.setattr(temporal_mocks.activity, "info", lambda: info)

    async def execute(scope, operation_id, inputs):
        return HTTPJSONActivityOutput(
            operation_id=operation_id, status_code=201, execution_mode="mock"
        )

    adapter = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    return HTTPJSONMockActivities(adapter, SCOPE, "nexus", "worker"), adapter, info


def assert_private(error, code):
    assert isinstance(error, ApplicationError)
    assert error.type == "HTTPMockDispatch." + code
    assert error.non_retryable and not error.details
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


@pytest.mark.asyncio
async def test_actual_context_and_private_input_map_to_canonical_metadata_only(worker):
    bridge, adapter, _ = worker
    encoded = await bridge.dispatch(call())
    result = json.loads(encoded)
    selected_scope, operation_id, inputs = adapter.execute.call_args.args
    assert selected_scope == SCOPE and inputs.execution_mode == "mock"
    assert inputs.path.endswith(PRIVATE)
    assert operation_id.startswith("http-") and len(operation_id) == 69 and ":" not in operation_id
    assert result == {"operation_id": operation_id, "status_code": 201, "execution_mode": "mock"}
    assert encoded == json.dumps(result, sort_keys=True, separators=(",", ":"))
    assert PRIVATE not in encoded


@pytest.mark.asyncio
async def test_retry_attempt_preserves_operation_and_new_run_changes_it(worker):
    bridge, _, info = worker
    first = json.loads(await bridge.dispatch(call()))["operation_id"]
    info.attempt = 2
    assert json.loads(await bridge.dispatch(call()))["operation_id"] == first
    info.workflow_run_id = "temporal-run-2"
    assert json.loads(await bridge.dispatch(call()))["operation_id"] != first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "other"),
        ("workflow_namespace", "other"),
        ("task_queue", "other"),
        ("workflow_type", "OtherWorkflow"),
        ("workflow_id", "forged"),
        ("activity_type", "other"),
        ("activity_id", "other-node"),
        ("workflow_namespace", None),
        ("workflow_type", None),
        ("workflow_id", None),
        ("workflow_run_id", None),
    ],
)
async def test_context_mismatch_is_private_nonretryable_before_adapter(worker, field, value):
    bridge, adapter, info = worker
    setattr(info, field, value)
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "forbidden")
    adapter.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["activity_id", "activity_version", "artifact_digest"])
async def test_descriptor_reference_mismatch_never_enters_adapter(worker, field):
    bridge, adapter, _ = worker
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call(**{field: PRIVATE}))
    assert_private(caught.value, "forbidden")
    adapter.execute.assert_not_called()


@pytest.mark.asyncio
async def test_live_input_never_enters_adapter(worker):
    bridge, adapter, _ = worker
    value = json.loads(call().input_json)
    value.update(execution_mode="live", fixture_digest=None)
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call(input_json=json.dumps(value)))
    assert_private(caught.value, "forbidden")
    adapter.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded",
    ["{", "[]", '{"a":1,"a":2}', '{"value":NaN}', "x" * 65537],
    ids=["invalid", "array", "duplicate", "nonfinite", "oversized"],
)
async def test_invalid_private_json_is_safely_rejected(worker, encoded):
    bridge, adapter, _ = worker
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call(input_json=encoded))
    assert_private(caught.value, "invalid_request")
    adapter.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "other"},
        {"operation_id": None},
        {"status_code": True},
        {"status_code": 199},
        {"status_code": 600},
        {"execution_mode": "live"},
        {"execution_mode": PRIVATE},
    ],
)
async def test_forged_provider_outputs_never_become_history_results(worker, changes):
    bridge, adapter, _ = worker

    async def forged(scope, operation_id, inputs):
        fields = {"operation_id": operation_id, "status_code": 200, "execution_mode": "mock"}
        return HTTPJSONActivityOutput.model_construct(**(fields | changes))

    adapter.execute.side_effect = forged
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "invalid_provider_result")


@pytest.mark.asyncio
async def test_raw_provider_fields_are_not_serialized(worker):
    bridge, adapter, _ = worker

    async def injected(scope, operation_id, inputs):
        output = HTTPJSONActivityOutput(
            operation_id=operation_id, status_code=200, execution_mode="mock"
        )
        object.__setattr__(output, "raw_body", PRIVATE)
        return output

    adapter.execute.side_effect = injected
    assert PRIVATE not in await bridge.dispatch(call())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["forbidden", "fixture_mismatch", "not_found", "timeout", "secret-code"]
)
async def test_adapter_errors_use_closed_nonretryable_codes(worker, code):
    bridge, adapter, _ = worker
    error = HTTPActivityError("unavailable")
    error.code = code
    error.args = (PRIVATE,)
    adapter.execute.side_effect = error
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "unavailable" if code == "secret-code" else code)


@pytest.mark.asyncio
async def test_unknown_provider_exception_is_not_history_error_text(worker):
    bridge, adapter, _ = worker
    adapter.execute.side_effect = RuntimeError(PRIVATE)
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress", [False, True])
async def test_timeout_is_private_even_when_adapter_suppresses_cancellation(worker, suppress):
    bridge, adapter, _ = worker
    bridge.timeout_seconds = 0.01

    async def stalled(scope, operation_id, inputs):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not suppress:
                raise
            return HTTPJSONActivityOutput(
                operation_id=operation_id, status_code=200, execution_mode="mock"
            )

    adapter.execute.side_effect = stalled
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress", [False, True])
async def test_caller_cancellation_propagates(worker, suppress):
    bridge, adapter, _ = worker
    entered = asyncio.Event()

    async def stalled(scope, operation_id, inputs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not suppress:
                raise
            return HTTPJSONActivityOutput(
                operation_id=operation_id, status_code=200, execution_mode="mock"
            )

    adapter.execute.side_effect = stalled
    task = asyncio.create_task(bridge.dispatch(call()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_late_sync_adapter_cannot_return_success(worker):
    bridge, adapter, _ = worker
    bridge.timeout_seconds = 0.005

    async def delayed(scope, operation_id, inputs):
        time.sleep(0.02)
        return HTTPJSONActivityOutput(
            operation_id=operation_id, status_code=200, execution_mode="mock"
        )

    adapter.execute.side_effect = delayed
    with pytest.raises(ApplicationError) as caught:
        await bridge.dispatch(call())
    assert_private(caught.value, "timeout")


@pytest.mark.parametrize("timeout", [False, 0, -1, 31, math.nan, math.inf, "30"])
def test_invalid_timeout_configuration_is_rejected(timeout):
    with pytest.raises(ValueError, match="Invalid HTTP mock worker configuration"):
        HTTPJSONMockActivities(SimpleNamespace(), SCOPE, "nexus", "worker", timeout_seconds=timeout)


@pytest.mark.parametrize(
    "namespace,queue",
    [
        ("", "worker"),
        ("bad namespace", "worker"),
        ("nexus", ""),
        ("nexus", "\n"),
        ("nexus", " worker"),
        ("nexus", "worker "),
    ],
)
def test_invalid_placement_is_rejected(namespace, queue):
    with pytest.raises(ValueError, match="Invalid HTTP mock worker configuration"):
        HTTPJSONMockActivities(SimpleNamespace(), SCOPE, namespace, queue)


def test_scope_configuration_is_snapshotted():
    scope = Scope(organization_id="acme", environment_id="dev")
    bridge = HTTPJSONMockActivities(SimpleNamespace(), scope, "nexus", "worker")
    object.__setattr__(scope, "environment_id", "prod")
    assert bridge.scope == SCOPE
