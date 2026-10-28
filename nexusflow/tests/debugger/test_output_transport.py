"""Exact-run, bounded output transport without leaking query failure payloads."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowQueryFailedError

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import DebugStepOutput, Snapshot
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment


@pytest.fixture
def output_adapter(tmp_path):
    snapshot = Snapshot("release", "digest", "proof.v1", "later", "paused", 1, ("effect",))
    value = DebugStepOutput(snapshot, "effect", '{"public":1,"secret":"private"}')
    handle = SimpleNamespace(query=AsyncMock(return_value=value))
    client = SimpleNamespace(namespace="acme-dev", get_workflow_handle=Mock(return_value=handle))
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), "acme-dev", "queue"
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")
    try:
        yield (
            TemporalDebugRuntime(AsyncMock(return_value=client), journal),
            placement,
            client,
            handle,
        )
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_selected_output_pins_exact_run_and_bounded_query_only(output_adapter):
    runtime, placement, client, handle = output_adapter
    value = await runtime.step_output(placement, "execution", "exact-run", "effect")
    assert value.output_json == '{"public":1,"secret":"private"}'
    client.get_workflow_handle.assert_called_once_with("execution", run_id="exact-run")
    handle.query.assert_awaited_once_with(
        ProofWorkflow.step_output, "effect", rpc_timeout=runtime.rpc_timeout
    )
    assert runtime.rpc_timeout.total_seconds() == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_non_dev_denied_before_client_or_query(output_adapter, environment):
    runtime, placement, client, handle = output_adapter
    placement = replace(placement, scope=Scope(organization_id="acme", environment_id=environment))
    with pytest.raises(DebugError) as error:
        await runtime.step_output(placement, "execution", "run", "effect")
    assert error.value.code == "forbidden"
    runtime.clients.assert_not_called()
    client.get_workflow_handle.assert_not_called()
    handle.query.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [WorkflowQueryFailedError("private"), TimeoutError("private")])
async def test_query_and_transport_failures_have_safe_codes(output_adapter, failure):
    runtime, placement, _, handle = output_adapter
    handle.query.side_effect = failure
    with pytest.raises(DebugError) as error:
        await runtime.step_output(placement, "execution", "run", "effect")
    assert error.value.code == (
        "conflict" if isinstance(failure, WorkflowQueryFailedError) else "unavailable"
    )
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_empty_run_or_wrong_namespace_never_queries(output_adapter):
    runtime, placement, client, handle = output_adapter
    with pytest.raises(DebugError):
        await runtime.step_output(placement, "execution", "", "effect")
    client.namespace = "foreign"
    with pytest.raises(DebugError):
        await runtime.step_output(placement, "execution", "run", "effect")
    handle.query.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["type", "snapshot", "node", "pending", "payload", "bytes"])
async def test_invalid_runtime_output_is_rejected_before_return(output_adapter, mutation):
    runtime, placement, _, handle = output_adapter
    value = handle.query.return_value
    changes = {
        "type": {},
        "snapshot": replace(value, snapshot=None),
        "node": replace(value, node_id="other"),
        "pending": replace(value, snapshot=replace(value.snapshot, completed_nodes=())),
        "payload": replace(value, output_json={"secret": "private"}),
        "bytes": replace(value, output_json="é" * 32769),
    }
    handle.query.return_value = changes[mutation]
    with pytest.raises(DebugError) as error:
        await runtime.step_output(placement, "execution", "run", "effect")
    assert error.value.code == "unavailable" and "private" not in str(error.value)
