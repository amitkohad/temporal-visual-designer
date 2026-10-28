"""Independent cancellation transport recovery and exact-run boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import CommandReceipt, DebugCommand
from nexusflow.modules.identity_environments.types import Environment


@pytest.fixture
def transport(tmp_path):
    receipt = CommandReceipt("stop", 4)
    handle = SimpleNamespace(
        execute_update=AsyncMock(return_value=receipt),
        get_update_handle=Mock(
            return_value=SimpleNamespace(result=AsyncMock(return_value=receipt))
        ),
        cancel=AsyncMock(),
    )
    client = SimpleNamespace(namespace="acme-dev", get_workflow_handle=Mock(return_value=handle))
    placement = Environment(
        Scope(organization_id="acme", environment_id="dev"), "acme-dev", "queue"
    )
    journal = SQLiteDispatchJournal(tmp_path / "dispatch.db")
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
async def test_cancel_retry_recovers_update_before_retrying_exact_run_rpc(transport):
    runtime, placement, client, handle = transport
    command = DebugCommand("stop", "approval", "cancel", 3)
    events = []

    async def accept(*args, **kwargs):
        events.append("accept")
        return CommandReceipt("stop", 4)

    async def cancel(**kwargs):
        events.append("cancel")
        if events.count("cancel") == 1:
            raise TimeoutError("sensitive transport diagnostics")

    handle.execute_update.side_effect = accept
    handle.cancel.side_effect = cancel
    with pytest.raises(DebugError) as error:
        await runtime.control(placement, "execution", "pinned-run", command)
    assert error.value.code == "unavailable"
    assert "sensitive" not in str(error.value)
    first_update_id = handle.execute_update.call_args.kwargs["id"]

    handle.execute_update.side_effect = TimeoutError("lost update response")
    assert await runtime.control(placement, "execution", "pinned-run", command) == CommandReceipt(
        "stop", 4
    )
    assert events == ["accept", "cancel", "cancel"]
    assert handle.execute_update.call_args.kwargs["id"] == first_update_id
    assert handle.get_update_handle.call_args.kwargs["workflow_run_id"] == "pinned-run"
    assert all(
        call.kwargs == {"run_id": "pinned-run"}
        for call in client.get_workflow_handle.call_args_list
    )
    assert all(
        call.kwargs["rpc_timeout"].total_seconds() == 10 for call in handle.cancel.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["continue", "step_over"])
async def test_other_controls_never_request_temporal_cancellation(transport, action):
    runtime, placement, _, handle = transport
    await runtime.control(
        placement, "execution", "pinned-run", DebugCommand("stop", "node", action, 3)
    )
    handle.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_or_invalid_cancel_receipts_cannot_cancel_a_run(transport):
    runtime, placement, _, handle = transport
    command = DebugCommand("stop", "node", "cancel", 3)
    handle.execute_update.side_effect = WorkflowUpdateFailedError(
        ApplicationError("private", type="Conflict")
    )
    with pytest.raises(DebugError) as error:
        await runtime.control(placement, "execution", "pinned-run", command)
    assert error.value.code == "conflict"
    handle.cancel.assert_not_called()
    handle.get_update_handle.assert_not_called()

    handle.execute_update.side_effect = None
    handle.execute_update.return_value = CommandReceipt("wrong-command", 4)
    with pytest.raises(DebugError):
        await runtime.control(placement, "execution", "pinned-run", command)
    handle.cancel.assert_not_called()
