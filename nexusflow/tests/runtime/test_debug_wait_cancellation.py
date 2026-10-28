"""Wait-boundary cancellation eligibility, races and the post-timer dispatch gate."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError

from nexusflow.modules.durable_runtime import workflow as runtime_module
from nexusflow.modules.durable_runtime.types import (
    ApprovalCommand,
    DebugCommand,
    TaskCompletionCommand,
)
from nexusflow.tests.runtime.test_debug_cancellation import paused


@pytest.fixture(autouse=True)
def new_wait_cancellation_history(monkeypatch):
    monkeypatch.setattr(runtime_module.workflow, "patched", lambda patch_id: True)


@pytest.mark.parametrize("state", ["waiting_timer", "waiting_approval"])
def test_legacy_history_preserves_rejected_wait_cancellation(monkeypatch, state):
    instance = paused()
    instance.state = state
    patches = []

    def old_history(patch_id):
        patches.append(patch_id)
        return False

    monkeypatch.setattr(runtime_module.workflow, "patched", old_history)
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("cancel", "timer", "cancel", 0))
    assert patches == ["debug-wait-cancellation-v1"]
    assert instance.state == state
    assert not instance.cancellation_requested
    assert instance.revision == 0
    assert instance.receipts == {}


def test_existing_paused_cancellation_needs_no_new_history_marker(monkeypatch):
    def unexpected_patch(_patch_id):
        raise AssertionError("Paused cancellation must preserve its old history")

    monkeypatch.setattr(runtime_module.workflow, "patched", unexpected_patch)
    instance = paused()
    assert instance.control(DebugCommand("cancel", "timer", "cancel", 0)).revision == 1


@pytest.mark.parametrize("state", ["paused", "waiting_timer", "waiting_approval"])
def test_wait_cancel_is_durable_and_does_not_fabricate_approval(state):
    instance = paused()
    instance.state = state
    command = DebugCommand("cancel", "timer", "cancel", 0)
    receipt = instance.control(command)
    assert instance.control(command) == receipt
    assert instance.cancellation_requested
    assert instance.state == "cancelling"
    assert instance.node_id == "timer"
    assert instance.approved is None
    assert instance.permit == ""
    assert instance.completed == []


@pytest.mark.parametrize(
    ("state", "managed", "approved"),
    [
        ("starting", False, None),
        ("running", False, None),
        ("creating_task", True, None),
        ("waiting_approval", True, None),
        ("waiting_approval", False, True),
        ("waiting_approval", False, False),
        ("cancelling", False, None),
        ("cancelled", False, None),
        ("completed", False, None),
        ("failed", False, None),
    ],
)
def test_unsupported_boundary_does_not_record_cancel_intent(state, managed, approved):
    instance = paused()
    instance.state, instance.managed_approval, instance.approved = state, managed, approved
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("cancel", "timer", "cancel", 0))
    assert instance.state == state
    assert not instance.cancellation_requested
    assert instance.revision == 0
    assert instance.receipts == {}


@pytest.mark.parametrize("state", ["waiting_timer", "waiting_approval"])
@pytest.mark.parametrize("action", ["step_over", "continue"])
def test_waits_cannot_be_advanced_by_debug_controls(state, action):
    instance = paused()
    instance.state = state
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("control", "timer", action, 0))
    assert instance.approved is None
    assert instance.permit == ""
    assert instance.revision == 0


@pytest.mark.parametrize("state", ["waiting_timer", "waiting_approval"])
@pytest.mark.parametrize(
    "command",
    [DebugCommand("cancel", "other", "cancel", 0), DebugCommand("cancel", "timer", "cancel", 1)],
)
def test_wait_cancel_checks_node_and_revision(state, command):
    instance = paused()
    instance.state = state
    with pytest.raises(ApplicationError):
        instance.control(command)
    assert not instance.cancellation_requested
    assert instance.state == state
    assert instance.revision == 0


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_wait_cancel_rejects_non_dev_even_with_debug_plan(environment):
    instance = paused()
    instance.state = "waiting_timer"
    instance.plan = replace(
        instance.plan, binding=replace(instance.plan.binding, environment_id=environment)
    )
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("cancel", "timer", "cancel", 0))
    assert not instance.cancellation_requested


def test_non_debug_wait_rejects_cancel():
    instance = paused()
    instance.state = "waiting_timer"
    instance.plan = replace(instance.plan, debug=False)
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("cancel", "timer", "cancel", 0))
    assert not instance.cancellation_requested


def test_cancel_winning_race_rejects_later_approval_or_completion(monkeypatch):
    instance = paused()
    instance.state = "waiting_approval"
    instance.control(DebugCommand("cancel", "timer", "cancel", 0))
    with pytest.raises(ApplicationError):
        instance.approve(ApprovalCommand("approve", "timer", True, 1))
    monkeypatch.setattr(
        runtime_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="execution", run_id="run"),
    )
    command = TaskCompletionCommand(
        "completion",
        "task",
        "acme",
        "dev",
        "execution",
        "run",
        "timer",
        instance.plan.definition_digest,
        instance.plan.binding.release_id,
        "approved",
        2,
    )
    with pytest.raises(ApplicationError):
        instance.complete_task(command)
    assert instance.approved is None
    assert instance.revision == 1
    assert instance.state == "cancelling"


def test_approval_winning_race_rejects_later_cancel():
    instance = paused()
    instance.state = "waiting_approval"
    receipt = instance.approve(ApprovalCommand("approve", "timer", True, 0))
    with pytest.raises(ApplicationError):
        instance.control(DebugCommand("cancel", "timer", "cancel", receipt.revision))
    assert instance.approved is True
    assert not instance.cancellation_requested
    assert instance.revision == 1


@pytest.mark.asyncio
async def test_timer_expiry_after_acceptance_remains_blocked_before_step_completion(monkeypatch):
    instance = paused()
    instance.stepping = False
    gates = []

    async def timer(_duration):
        assert instance.state == "waiting_timer"
        instance.control(DebugCommand("cancel", "timer", "cancel", 0))
        # Timer expiry races ahead of the separate exact-run cancellation RPC.

    async def gate(predicate):
        assert not predicate()
        gates.append((instance.node_id, instance.state, tuple(instance.completed)))
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime_module.workflow, "sleep", timer)
    monkeypatch.setattr(runtime_module.workflow, "wait_condition", gate)
    with pytest.raises(asyncio.CancelledError):
        await instance.run(instance.plan)
    assert gates == [("timer", "cancelling", ("start",))]
    assert instance.completed == ["start"]
    assert instance.state == "cancelled"


def test_stale_managed_approval_flag_does_not_block_later_timer_or_paused_boundary():
    for state in ("paused", "waiting_timer"):
        instance = paused()
        instance.state, instance.managed_approval = state, True
        instance.approved = True
        assert instance.control(DebugCommand("cancel", "timer", "cancel", 0)).revision == 1
