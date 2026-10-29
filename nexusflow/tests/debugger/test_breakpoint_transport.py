"""Exact-run breakpoint replacement and bounded retry-safe metadata transport."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowQueryFailedError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    DebugBreakpointCommand,
    DebugBreakpointState,
    Snapshot,
)
from nexusflow.modules.identity_environments.types import Environment


@pytest.fixture
def transport(tmp_path):
    state = DebugBreakpointState(
        Snapshot("release", "digest", "proof.v1", "first", "paused", 0, ()), ("second",)
    )
    handle = SimpleNamespace(
        query=AsyncMock(return_value=state),
        execute_update=AsyncMock(return_value=CommandReceipt("replace", 1)),
        get_update_handle=Mock(
            return_value=SimpleNamespace(
                result=AsyncMock(return_value=CommandReceipt("replace", 1))
            )
        ),
        cancel=AsyncMock(),
    )
    client = SimpleNamespace(namespace="dev", get_workflow_handle=Mock(return_value=handle))
    placement = Environment(Scope(organization_id="acme", environment_id="dev"), "dev", "queue")
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")
    try:
        yield (
            TemporalDebugRuntime(AsyncMock(return_value=client), journal),
            placement,
            client,
            handle,
            state,
        )
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_query_and_replace_are_bounded_and_pin_exact_run(transport):
    runtime, placement, client, handle, state = transport
    assert await runtime.breakpoints(placement, "execution", "exact-run") == state
    command = DebugBreakpointCommand("replace", "first", ("second",), 0)
    assert await runtime.replace_breakpoints(
        placement, "execution", "exact-run", command
    ) == CommandReceipt("replace", 1)
    assert all(
        call.kwargs == {"run_id": "exact-run"} for call in client.get_workflow_handle.call_args_list
    )
    assert handle.query.call_args.kwargs["rpc_timeout"].total_seconds() == 10
    assert handle.execute_update.call_args.kwargs["rpc_timeout"].total_seconds() == 10
    assert handle.execute_update.call_args.kwargs["id"].startswith("nexusflow-debug-breakpoints-")
    handle.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_lost_ack_reconciles_same_update_result_without_cancellation(transport):
    runtime, placement, _, handle, _ = transport
    command = DebugBreakpointCommand("replace", "first", ("second",), 0)
    await runtime.replace_breakpoints(placement, "execution", "exact-run", command)
    update_id = handle.execute_update.call_args.kwargs["id"]
    handle.execute_update.side_effect = TimeoutError("private diagnostics")
    assert await runtime.replace_breakpoints(
        placement, "execution", "exact-run", command
    ) == CommandReceipt("replace", 1)
    assert handle.get_update_handle.call_args.args == (update_id,)
    assert handle.get_update_handle.call_args.kwargs["workflow_run_id"] == "exact-run"
    await runtime.replace_breakpoints(
        placement, "execution", "exact-run", replace(command, breakpoints=())
    )
    assert handle.execute_update.call_args.kwargs["id"] != update_id
    handle.cancel.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "not-state",
        "not-snapshot",
        "bad-revision",
        "bool-revision",
        "list",
        "duplicate",
        "unsorted",
        "too-many",
        "invalid-id",
    ],
)
async def test_invalid_query_metadata_fails_closed(transport, change):
    runtime, placement, _, handle, state = transport
    if change == "not-state":
        state = object()
    elif change == "not-snapshot":
        state = replace(state, snapshot=object())
    elif change == "bad-revision":
        state = replace(state, snapshot=replace(state.snapshot, revision=-1))
    elif change == "bool-revision":
        state = replace(state, snapshot=replace(state.snapshot, revision=True))
    elif change == "list":
        state = replace(state, breakpoints=["second"])
    elif change == "duplicate":
        state = replace(state, breakpoints=("second", "second"))
    elif change == "unsorted":
        state = replace(state, breakpoints=("z", "a"))
    elif change == "too-many":
        state = replace(state, breakpoints=tuple(f"n{i:03}" for i in range(101)))
    else:
        state = replace(state, breakpoints=("private/value",))
    handle.query.return_value = state
    with pytest.raises(DebugError) as error:
        await runtime.breakpoints(placement, "execution", "exact-run")
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [-1, 0, True, 2**63])
async def test_invalid_update_receipt_is_unavailable(transport, revision):
    runtime, placement, _, handle, _ = transport
    handle.execute_update.return_value = CommandReceipt("replace", revision)
    with pytest.raises(DebugError) as error:
        await runtime.replace_breakpoints(
            placement, "execution", "exact-run", DebugBreakpointCommand("replace", "first", (), 0)
        )
    assert error.value.code == "unavailable"
    handle.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_conflicts_are_safe_and_not_reconciled(transport):
    runtime, placement, _, handle, _ = transport
    handle.execute_update.side_effect = WorkflowUpdateFailedError(
        ApplicationError("private payload", type="Conflict")
    )
    with pytest.raises(DebugError) as error:
        await runtime.replace_breakpoints(
            placement, "execution", "exact-run", DebugBreakpointCommand("replace", "first", (), 0)
        )
    assert error.value.code == "conflict" and "private" not in str(error.value)
    handle.get_update_handle.assert_not_called()
    handle.query.side_effect = WorkflowQueryFailedError("private query payload")
    with pytest.raises(DebugError) as error:
        await runtime.breakpoints(placement, "execution", "exact-run")
    assert error.value.code == "conflict" and "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_non_dev_rejected_before_remote_client(transport, environment):
    runtime, placement, _, _, _ = transport
    placement = replace(placement, scope=Scope(organization_id="acme", environment_id=environment))
    with pytest.raises(DebugError):
        await runtime.breakpoints(placement, "execution", "exact-run")
    with pytest.raises(DebugError):
        await runtime.replace_breakpoints(
            placement, "execution", "exact-run", DebugBreakpointCommand("replace", "first", (), 0)
        )
    runtime.clients.assert_not_called()


@pytest.mark.asyncio
async def test_empty_run_and_namespace_mismatch_fail_before_handle(transport):
    runtime, placement, client, _, _ = transport
    with pytest.raises(DebugError):
        await runtime.breakpoints(placement, "execution", "")
    with pytest.raises(DebugError):
        await runtime.replace_breakpoints(
            placement, "execution", "", DebugBreakpointCommand("replace", "first", (), 0)
        )
    client.namespace = "wrong"
    with pytest.raises(DebugError):
        await runtime.breakpoints(placement, "execution", "exact-run")
    client.get_workflow_handle.assert_not_called()


@pytest.mark.parametrize(
    "case", ["pending-permit", "cancelling", "normal", "prod", "bool-revision", "bad-id"]
)
def test_workflow_guards_do_not_mutate_overlay_or_release_gate(case):
    from nexusflow.tests.runtime.test_debug_cancellation import paused

    workflow = paused()
    command = DebugBreakpointCommand("replace", "timer", ("timer",), 0)
    if case == "pending-permit":
        workflow.permit = "timer"
    elif case == "cancelling":
        workflow.state = "cancelling"
    elif case == "normal":
        workflow.plan = replace(workflow.plan, debug=False)
    elif case == "prod":
        workflow.plan = replace(
            workflow.plan, binding=replace(workflow.plan.binding, environment_id="prod")
        )
    elif case == "bool-revision":
        command = replace(command, expected_revision=False)
    else:
        command = replace(command, command_id="not/valid")
    before = (workflow.state, workflow.permit, workflow.breakpoints, workflow.revision)
    with pytest.raises(ApplicationError):
        workflow.replace_breakpoints(command)
    assert (workflow.state, workflow.permit, workflow.breakpoints, workflow.revision) == before
    assert workflow.receipts == {}


@pytest.mark.parametrize("targets", [(), ("timer",)])
def test_workflow_receipts_share_namespace_and_overlay_change_keeps_gate(targets):
    from nexusflow.modules.durable_runtime.types import DebugCommand
    from nexusflow.tests.runtime.test_debug_cancellation import paused

    workflow = paused()
    command = DebugBreakpointCommand("shared", "timer", targets, 0)
    receipt = workflow.replace_breakpoints(command)
    assert receipt.revision == 1
    assert workflow.replace_breakpoints(command) == receipt
    assert workflow.breakpoints == targets
    assert workflow.permit == "" and workflow.state == "paused"
    with pytest.raises(ApplicationError):
        workflow.control(DebugCommand("shared", "timer", "continue", 1))
    assert workflow.permit == "" and workflow.revision == 1
