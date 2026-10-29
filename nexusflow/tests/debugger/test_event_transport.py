"""Bounded exact-run event transport and payload-free deterministic recording."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from temporalio.client import WorkflowQueryFailedError
from temporalio.exceptions import ApplicationError

from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.temporal import TemporalDebugRuntime
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.modules.durable_runtime.types import (
    DebugEventBatch,
    DebugEventQuery,
    DebugRuntimeEvent,
    Snapshot,
)
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.tests.runtime.test_debug_breakpoints_integration import plan


@pytest.fixture
def transport(tmp_path):
    snapshot = Snapshot("release", "digest", "proof.v1", "first", "paused", 0, ())
    batch = DebugEventBatch(
        snapshot, (DebugRuntimeEvent(1, "paused", "first", "paused", 0),), 1, 1, 1, False, False
    )
    handle = SimpleNamespace(query=AsyncMock(return_value=batch))
    client = SimpleNamespace(namespace="dev", get_workflow_handle=Mock(return_value=handle))
    placement = Environment(Scope(organization_id="acme", environment_id="dev"), "dev", "queue")
    journal = SQLiteDispatchJournal(tmp_path / "journal.db")
    try:
        yield (
            TemporalDebugRuntime(AsyncMock(return_value=client), journal),
            placement,
            client,
            handle,
            batch,
        )
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_exact_run_bounded_query_is_read_only(transport):
    runtime, placement, client, handle, batch = transport
    assert await runtime.events(placement, "execution", "pinned", DebugEventQuery()) == batch
    assert client.get_workflow_handle.call_args.kwargs == {"run_id": "pinned"}
    assert handle.query.call_args.kwargs["rpc_timeout"].total_seconds() == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        DebugEventQuery(-1),
        DebugEventQuery(True),
        DebugEventQuery(limit=0),
        DebugEventQuery(limit=26),
        DebugEventQuery(limit=True),
    ],
)
async def test_invalid_queries_never_reach_remote(transport, query):
    runtime, placement, _, handle, _ = transport
    with pytest.raises(DebugError) as error:
        await runtime.events(placement, "execution", "pinned", query)
    assert error.value.code == "invalid_request"
    handle.query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"events": ()},
        {"earliest_sequence": 0},
        {"latest_sequence": -1},
        {"next_after": True},
        {"next_after": 0},
        {"gap": True},
        {"has_more": True},
        {"events": (DebugRuntimeEvent(2, "paused", "first", "paused", 0),)},
        {"events": (DebugRuntimeEvent(1, "secret-value", "first", "paused", 0),)},
        {"events": (DebugRuntimeEvent(1, "paused", "../secret", "paused", 0),)},
        {"events": (DebugRuntimeEvent(1, "paused", "first", "secret-value", 0),)},
        {"events": (DebugRuntimeEvent(1, "paused", "first", "paused", True),)},
    ],
)
async def test_malformed_pages_fail_safely(transport, change):
    runtime, placement, _, handle, batch = transport
    handle.query.return_value = replace(batch, **change)
    with pytest.raises(DebugError) as error:
        await runtime.events(placement, "execution", "pinned", DebugEventQuery())
    assert error.value.code == "unavailable" and "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_unavailable_and_query_failure_do_not_echo_messages(transport):
    runtime, placement, _, handle, _ = transport
    for exception, code in [
        (OSError("private"), "unavailable"),
        (WorkflowQueryFailedError("private"), "conflict"),
    ]:
        handle.query.side_effect = exception
        with pytest.raises(DebugError) as error:
            await runtime.events(placement, "execution", "pinned", DebugEventQuery())
        assert error.value.code == code and "private" not in str(error.value)


def test_recording_retention_and_cursor_rules_do_not_change_snapshot():
    instance = ProofWorkflow()
    instance.plan = plan()
    instance.node_id, instance.state = "first", "paused"
    for _ in range(300):
        instance._event("paused")
    snapshot = instance.snapshot()
    batch = instance.debug_events(DebugEventQuery())
    assert (batch.earliest_sequence, batch.latest_sequence) == (45, 300)
    assert batch.gap and batch.has_more and len(batch.events) == 25
    assert batch.events[0].sequence == 45 and batch.next_after == 69
    later = instance.debug_events(DebugEventQuery(299))
    assert not later.gap and not later.has_more and later.next_after == 300
    empty = instance.debug_events(DebugEventQuery(300))
    assert not empty.events and empty.next_after == 300
    assert instance.snapshot() == snapshot
    assert len(instance.event_log) == 256
    with pytest.raises(ApplicationError):
        instance.debug_events(DebugEventQuery(301))


def test_normal_runs_never_record_or_expose_events():
    instance = ProofWorkflow()
    instance.plan = plan(debug=False)
    instance._event("paused")
    assert instance.event_log == [] and instance.event_sequence == 0
    with pytest.raises(ApplicationError):
        instance.debug_events(DebugEventQuery())
