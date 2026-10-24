"""Async completion transport preserves durable outbox acknowledgement semantics."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from nexusflow.modules.human_tasks.types import Completion, TaskError
from nexusflow.tests.human_tasks.test_acceptance import (
    OTHER,
    SCOPE,
    decision,
    request,
    service,
)


class Sink:
    def __init__(self) -> None:
        self.accepted: dict[str, Completion] = {}
        self.fail = False

    async def deliver(self, completion: Completion) -> None:
        await asyncio.sleep(0)
        self.accepted.setdefault(completion.completion_id, completion)
        if self.fail:
            raise RuntimeError("private destination failure")


@pytest.mark.asyncio
async def test_async_acknowledgement_retries_same_id_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = service(path)
    task = core.create(SCOPE, request())
    core.decide(SCOPE, task.task_id, decision("yes", 1))
    pending = core.pending_completions(SCOPE)
    sink = Sink()
    sink.fail = True
    try:
        with pytest.raises(TaskError) as error:
            await core.deliver_pending_async(SCOPE, sink)
        assert error.value.code == "delivery_unavailable"
        assert "private destination failure" not in str(error.value)
        assert core.pending_completions(SCOPE) == pending
    finally:
        core.close()
    reopened = service(path)
    try:
        sink.fail = False
        assert await reopened.deliver_pending_async(OTHER, sink) == 0
        assert reopened.pending_completions(SCOPE) == pending
        assert await reopened.deliver_pending_async(SCOPE, sink) == 1
        assert await reopened.deliver_pending_async(SCOPE, sink) == 0
        assert tuple(sink.accepted.values()) == pending
        assert reopened.audit(SCOPE, task.task_id)[-1].kind == "completion_delivered"
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_holding_transaction(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    task = core.create(SCOPE, request())
    core.decide(SCOPE, task.task_id, decision("yes", 1))
    started = asyncio.Event()

    class BlockingSink:
        async def deliver(self, completion: Completion) -> None:
            started.set()
            await asyncio.Event().wait()

    dispatch = asyncio.create_task(core.deliver_pending_async(SCOPE, BlockingSink()))
    try:
        await started.wait()
        # A separate thread must read while the coroutine is awaiting the sink.
        assert await asyncio.wait_for(asyncio.to_thread(core.pending_completions, SCOPE), 2)
        assert not core._db.in_transaction
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        assert len(core.pending_completions(SCOPE)) == 1
        assert all(e.kind != "completion_delivered" for e in core.audit(SCOPE, task.task_id))
        assert await core.deliver_pending_async(SCOPE, Sink()) == 1
    finally:
        dispatch.cancel()
        core.close()


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_async_acknowledgement(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = service(path)
    try:
        task = core.create(SCOPE, request())
        core.decide(SCOPE, task.task_id, decision("yes", 1))
        with sqlite3.connect(path) as fault:
            fault.execute(
                "CREATE TRIGGER fail_delivery BEFORE INSERT ON human_events "
                "BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
            )
        sink = Sink()
        with pytest.raises(sqlite3.DatabaseError):
            await core.deliver_pending_async(SCOPE, sink)
        assert len(core.pending_completions(SCOPE)) == 1
        assert all(e.kind != "completion_delivered" for e in core.audit(SCOPE, task.task_id))
        with sqlite3.connect(path) as fault:
            fault.execute("DROP TRIGGER fail_delivery")
        assert await core.deliver_pending_async(SCOPE, sink) == 1
        assert len(sink.accepted) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_concurrent_connections_commit_one_delivery_event(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    first = service(path)
    second = service(path)
    task = first.create(SCOPE, request())
    first.decide(SCOPE, task.task_id, decision("yes", 1))
    entered = 0
    both = asyncio.Event()

    class SharedSink(Sink):
        async def deliver(self, completion: Completion) -> None:
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await both.wait()
            await super().deliver(completion)

    sink = SharedSink()
    try:
        counts = await asyncio.wait_for(
            asyncio.gather(
                first.deliver_pending_async(SCOPE, sink),
                second.deliver_pending_async(SCOPE, sink),
            ),
            3,
        )
        assert sorted(counts) == [0, 1]
        assert entered == 2
        assert len(sink.accepted) == 1
        assert first.pending_completions(SCOPE) == ()
        assert sum(e.kind == "completion_delivered" for e in first.audit(SCOPE, task.task_id)) == 1
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_async_dispatch_preserves_scoped_pending_order(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        for index in range(3):
            task = core.create(
                SCOPE,
                request().model_copy(update={"node_invocation_id": f"node-{index}"}),
            )
            core.decide(SCOPE, task.task_id, decision(f"yes-{index}", 1))
        other = core.create(OTHER, request())
        core.decide(OTHER, other.task_id, decision("other", 1))
        pending = core.pending_completions(SCOPE)
        sink = Sink()
        assert await core.deliver_pending_async(SCOPE, sink) == 3
        assert tuple(sink.accepted.values()) == pending
        assert len(core.pending_completions(OTHER)) == 1
    finally:
        core.close()
