"""Exact-run pinning and ambiguous Temporal command acknowledgement."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    DebugCommand,
    ExecutionPlan,
    ReleaseBinding,
    Snapshot,
)
from nexusflow.modules.identity_environments.types import Environment


@pytest.fixture
def adapter(tmp_path):
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), "acme-dev", "queue"
    )
    snapshot = Snapshot("release", "digest", "proof.v1", "start", "paused", 0, ())
    handle = SimpleNamespace(
        query=AsyncMock(return_value=snapshot),
        execute_update=AsyncMock(return_value=CommandReceipt("next", 1)),
        get_update_handle=Mock(
            return_value=SimpleNamespace(result=AsyncMock(return_value=CommandReceipt("next", 1)))
        ),
    )
    client = SimpleNamespace(
        namespace="acme-dev",
        service_client=SimpleNamespace(config=SimpleNamespace(target_host="localhost")),
        get_workflow_handle=Mock(return_value=handle),
        start_workflow=AsyncMock(return_value=SimpleNamespace(result_run_id="exact-run")),
    )
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")
    runtime = TemporalDebugRuntime(AsyncMock(return_value=client), journal)
    yield runtime, placement, client, handle, journal
    journal.close()


@pytest.mark.asyncio
async def test_start_journals_exact_ack_and_retry_never_starts_again(adapter):
    runtime, placement, client, _, journal = adapter
    plan = ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "digest", "cfg"), "flow", "digest", (), debug=True
    )
    execution = "nf-" + "a" * 32
    assert journal.run_id(execution) is None
    assert await runtime.start(placement, execution, plan) == "exact-run"
    assert await runtime.start(placement, execution, plan) == "exact-run"
    assert client.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_snapshot_and_command_pin_the_acknowledged_run(adapter):
    runtime, placement, client, handle, _ = adapter
    assert (await runtime.snapshot(placement, "execution", "exact-run")).state == "paused"
    command = DebugCommand("next", "start", "step_over", 0)
    assert await runtime.control(placement, "execution", "exact-run", command) == CommandReceipt(
        "next", 1
    )
    assert all(
        call.kwargs == {"run_id": "exact-run"} for call in client.get_workflow_handle.call_args_list
    )
    assert handle.execute_update.call_args.kwargs["id"].startswith("nexusflow-debug-")
    assert handle.query.call_args.kwargs["rpc_timeout"].total_seconds() == 10


@pytest.mark.asyncio
async def test_lost_response_recovers_update_result_on_original_run(adapter):
    runtime, placement, _, handle, _ = adapter
    handle.execute_update.side_effect = TimeoutError("private payload")
    assert await runtime.control(
        placement, "execution", "exact-run", DebugCommand("next", "start", "continue", 0)
    ) == CommandReceipt("next", 1)
    assert handle.get_update_handle.call_args.kwargs["workflow_run_id"] == "exact-run"


@pytest.mark.asyncio
async def test_conflict_is_safe_and_does_not_poll_failed_update(adapter):
    runtime, placement, _, handle, _ = adapter
    handle.execute_update.side_effect = WorkflowUpdateFailedError(
        ApplicationError("private", type="Conflict")
    )
    with pytest.raises(DebugError) as error:
        await runtime.control(
            placement, "execution", "exact-run", DebugCommand("next", "start", "continue", 0)
        )
    assert error.value.code == "conflict" and "private" not in str(error.value)
    handle.get_update_handle.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_runtime_rejects_non_dev_before_remote_client(adapter, environment):
    runtime, placement, _, _, _ = adapter
    placement = replace(placement, scope=Scope(organization_id="acme", environment_id=environment))
    with pytest.raises(DebugError, match="Dev"):
        await runtime.snapshot(placement, "execution", "exact-run")
    runtime.clients.assert_not_called()


@pytest.mark.asyncio
async def test_namespace_mismatch_and_empty_run_fail_closed(adapter):
    runtime, placement, client, _, _ = adapter
    client.namespace = "other"
    with pytest.raises(DebugError):
        await runtime.snapshot(placement, "execution", "exact-run")
    client.get_workflow_handle.assert_not_called()
    client.namespace = placement.temporal_namespace
    with pytest.raises(DebugError):
        await runtime.snapshot(placement, "execution", "")
    client.get_workflow_handle.assert_not_called()


@pytest.mark.asyncio
async def test_changed_command_has_distinct_update_id_for_workflow_conflict_check(adapter):
    runtime, placement, _, handle, _ = adapter
    command = DebugCommand("next", "start", "continue", 0)
    await runtime.control(placement, "execution", "exact-run", command)
    await runtime.control(placement, "execution", "exact-run", command)
    await runtime.control(placement, "execution", "exact-run", replace(command, action="step_over"))
    updates = [call.kwargs["id"] for call in handle.execute_update.call_args_list]
    assert updates[0] == updates[1] and updates[0] != updates[2]
