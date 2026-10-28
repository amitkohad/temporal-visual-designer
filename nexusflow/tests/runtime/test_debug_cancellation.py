"""Paused cancellation preserves command identity and the blocked frontier."""

import pytest
from temporalio.exceptions import ApplicationError

from nexusflow.modules.durable_runtime.types import (
    DebugCommand,
    ExecutionPlan,
    ReleaseBinding,
    Step,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow


def paused():
    workflow = ProofWorkflow()
    workflow.plan = ExecutionPlan(
        ReleaseBinding("acme", "dev", "release", "sha256:" + "a" * 64, "config"),
        "workflow",
        "sha256:" + "b" * 64,
        (Step("start", "start"), Step("timer", "wait"), Step("end", "end")),
        debug=True,
    )
    workflow.state, workflow.node_id = "paused", "timer"
    return workflow


def test_cancel_receipt_is_stable_and_never_releases_paused_frontier():
    workflow = paused()
    command = DebugCommand("cancel", "timer", "cancel", 0)
    receipt = workflow.control(command)
    assert receipt.revision == 1
    assert workflow.state == "cancelling"
    assert workflow.permit == ""
    assert workflow.completed == []
    assert workflow.control(command) == receipt
    workflow.state = "cancelled"
    assert workflow.control(command) == receipt


@pytest.mark.parametrize(
    "command",
    [
        DebugCommand("cancel", "other", "cancel", 0),
        DebugCommand("cancel", "timer", "cancel", 1),
        DebugCommand("cancel", "timer", "unknown", 0),
    ],
)
def test_invalid_cancel_changes_neither_state_nor_revision(command):
    workflow = paused()
    with pytest.raises(ApplicationError):
        workflow.control(command)
    assert workflow.state == "paused"
    assert workflow.revision == 0
    assert workflow.permit == ""
    assert workflow.receipts == {}


@pytest.mark.parametrize("action", ["step_over", "continue", "cancel"])
def test_pending_cancel_rejects_new_commands_and_reused_identity(action):
    workflow = paused()
    workflow.control(DebugCommand("cancel", "timer", "cancel", 0))
    for command in (
        DebugCommand("another", "timer", action, 1),
        DebugCommand("cancel", "timer", action, 1),
    ):
        with pytest.raises(ApplicationError):
            workflow.control(command)
    assert workflow.state == "cancelling"
    assert workflow.revision == 1
    assert workflow.permit == ""
