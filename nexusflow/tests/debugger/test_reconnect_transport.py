"""One bounded exact-run query captures the complete reconnect baseline."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowQueryFailedError

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import DebugReconnectState, Snapshot
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_debug_events_integration import event_plan


@pytest.fixture
def transport(tmp_path):
    snapshot = Snapshot("release", "digest", "proof.v1", "wait-0", "paused", 0, ("start",))
    baseline = DebugReconnectState(snapshot, ("wait-1",), 1, 3)
    handle = SimpleNamespace(query=AsyncMock(return_value=baseline))
    client = SimpleNamespace(namespace="dev", get_workflow_handle=Mock(return_value=handle))
    placement = Environment(Scope(organization_id="acme", environment_id="dev"), "dev", "queue")
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")
    try:
        yield (
            TemporalDebugRuntime(AsyncMock(return_value=client), journal),
            placement,
            client,
            handle,
            baseline,
        )
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_only_one_exact_run_query_is_used(transport):
    runtime, placement, client, handle, baseline = transport
    assert await runtime.reconnect(placement, "execution", "pinned") == baseline
    handle.query.assert_awaited_once_with(
        ProofWorkflow.debug_reconnect, rpc_timeout=runtime.rpc_timeout
    )
    client.get_workflow_handle.assert_called_once_with("execution", run_id="pinned")
    assert runtime.rpc_timeout.total_seconds() == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"earliest_sequence": 0},
        {"earliest_sequence": True},
        {"latest_sequence": -1},
        {"latest_sequence": True},
        {"latest_sequence": 2**63},
        {"breakpoints": ("wait-1", "wait-1")},
        {"breakpoints": ("../private",)},
        {"breakpoints": ("z", "a")},
        {"breakpoints": ["wait-1"]},
    ],
)
async def test_malformed_metadata_is_never_returned(transport, change):
    runtime, placement, _, handle, baseline = transport
    handle.query.return_value = replace(baseline, **change)
    with pytest.raises(DebugError) as error:
        await runtime.reconnect(placement, "execution", "pinned")
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [True, -1, 2**63, 4])
async def test_snapshot_revision_cannot_exceed_cursor_or_bounds(transport, revision):
    runtime, placement, _, handle, baseline = transport
    handle.query.return_value = replace(
        baseline, snapshot=replace(baseline.snapshot, revision=revision)
    )
    with pytest.raises(DebugError):
        await runtime.reconnect(placement, "execution", "pinned")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"state": "private-value"},
        {"node_id": "../private"},
        {"node_id": ""},
        {"completed_nodes": ["start"]},
        {"completed_nodes": ("start", "start")},
        {"completed_nodes": ("../private",)},
    ],
)
async def test_unbounded_or_unknown_snapshot_metadata_fails_closed(transport, change):
    runtime, placement, _, handle, baseline = transport
    handle.query.return_value = replace(baseline, snapshot=replace(baseline.snapshot, **change))
    with pytest.raises(DebugError) as error:
        await runtime.reconnect(placement, "execution", "pinned")
    assert error.value.code == "unavailable" and "private-value" not in str(error.value)


@pytest.mark.asyncio
async def test_transport_and_query_errors_do_not_echo_messages(transport):
    runtime, placement, _, handle, _ = transport
    for failure, code in [
        (OSError("private-value"), "unavailable"),
        (WorkflowQueryFailedError("private-value"), "conflict"),
    ]:
        handle.query.side_effect = failure
        with pytest.raises(DebugError) as error:
            await runtime.reconnect(placement, "execution", "pinned")
        assert error.value.code == code and "private-value" not in str(error.value)


@pytest.mark.asyncio
async def test_empty_run_and_non_dev_never_query(transport):
    runtime, placement, _, handle, _ = transport
    with pytest.raises(DebugError):
        await runtime.reconnect(placement, "execution", "")
    for environment in ("uat", "prod"):
        foreign = replace(
            placement, scope=placement.scope.model_copy(update={"environment_id": environment})
        )
        with pytest.raises(DebugError):
            await runtime.reconnect(foreign, "execution", "pinned")
    handle.query.assert_not_awaited()


def test_workflow_baseline_is_readonly_and_immutable_after_progress():
    instance = ProofWorkflow()
    instance.plan = event_plan()
    instance.node_id, instance.state = "wait-0", "paused"
    instance.completed = ["start"]
    instance.breakpoints = ("wait-1", "wait-0", "wait-1")
    instance._event("paused")
    before = list(instance.event_log)
    baseline = instance.debug_reconnect()
    assert baseline.breakpoints == ("wait-0", "wait-1")
    assert baseline.latest_sequence == 1 and baseline.earliest_sequence == 1
    assert instance.event_log == before
    instance.completed.append("wait-0")
    instance.breakpoints = ()
    instance._event("step_completed")
    assert baseline.snapshot.completed_nodes == ("start",)
    assert baseline.latest_sequence == 1 and baseline.breakpoints == ("wait-0", "wait-1")


def test_workflow_baseline_carries_exact_retained_window():
    instance = ProofWorkflow()
    instance.plan = event_plan()
    instance.node_id, instance.state = "wait-0", "paused"
    for _ in range(300):
        instance._event("paused")
    baseline = instance.debug_reconnect()
    assert baseline.earliest_sequence == 45 and baseline.latest_sequence == 300
    assert len(instance.event_log) == 256
