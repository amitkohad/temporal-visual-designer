"""Fair bounded dispatch acceptance against durable SQLite outbox state."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import Completion
from nexusflow.tests.human_tasks.test_acceptance import (
    OTHER,
    SCOPE,
    decision,
    request,
    service,
)


def completed(core: TaskService, count: int = 3) -> list[Completion]:
    for number in range(count):
        task = core.create(
            SCOPE, request().model_copy(update={"node_invocation_id": f"node-{number}"})
        )
        core.decide(SCOPE, task.task_id, decision(f"vote-{number}", 1))
    return sorted(core.pending_completions(SCOPE), key=lambda row: row.task_id)


class Sink:
    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.accepted: set[str] = set()

    async def deliver(self, completion: Completion) -> None:
        self.calls.append(completion.task_id)
        self.accepted.add(completion.completion_id)
        if completion.task_id == self.fail:
            raise OSError("uncertain acknowledgement")


@pytest.mark.asyncio
async def test_failure_advances_cursor_and_does_not_starve_later_rows(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core)
        sink = Sink(rows[0].task_id)
        first = await core.dispatch_completion_batch(SCOPE, sink, limit=2)
        assert (first.attempted, first.delivered, first.failed) == (2, 1, 1)
        assert first.next_after_task_id == rows[1].task_id
        second = await core.dispatch_completion_batch(
            SCOPE, sink, limit=2, after_task_id=first.next_after_task_id
        )
        assert (second.attempted, second.delivered, second.failed) == (1, 1, 0)
        empty = await core.dispatch_completion_batch(
            SCOPE, sink, after_task_id=second.next_after_task_id
        )
        assert empty.attempted == 0 and empty.next_after_task_id is None
        sink.fail = None
        retry = await core.dispatch_completion_batch(
            SCOPE, sink, after_task_id=empty.next_after_task_id
        )
        assert retry.delivered == 1
        assert len(sink.accepted) == 3  # Stable IDs survive uncertain acknowledgements.
        assert core.pending_completions(SCOPE) == ()
        assert (
            len([e for e in core.audit(SCOPE, rows[0].task_id) if e.kind == "completion_delivered"])
            == 1
        )
    finally:
        core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["invalid-json", "foreign-scope", "foreign-task"])
async def test_corrupt_row_isolated_without_foreign_delivery_or_ack(
    tmp_path: Path, corruption: str
) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core, 2)
        bad = rows[0]
        payload = {
            "invalid-json": "{",
            "foreign-scope": bad.model_copy(update={"scope": OTHER}).model_dump_json(),
            "foreign-task": bad.model_copy(update={"task_id": rows[1].task_id}).model_dump_json(),
        }[corruption]
        with sqlite3.connect(core.database) as connection:
            connection.execute(
                "UPDATE human_outbox SET completion=? WHERE task=?", (payload, bad.task_id)
            )
        sink = Sink()
        result = await core.dispatch_completion_batch(SCOPE, sink)
        assert (result.attempted, result.delivered, result.failed) == (2, 1, 1)
        assert sink.calls == [rows[1].task_id]
        with sqlite3.connect(core.database) as connection:
            assert connection.execute(
                "SELECT delivered FROM human_outbox WHERE task=?", (bad.task_id,)
            ).fetchone() == (0,)
        other = await core.dispatch_completion_batch(OTHER, sink)
        assert other.attempted == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_timeout_retains_pending_and_continues(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core, 2)
        cancelled = asyncio.Event()

        class SlowSink(Sink):
            async def deliver(self, completion: Completion) -> None:
                if completion.task_id == rows[0].task_id:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                await super().deliver(completion)

        sink = SlowSink()
        result = await core.dispatch_completion_batch(SCOPE, sink, delivery_timeout=0.01)
        assert cancelled.is_set()
        assert (result.delivered, result.failed) == (1, 1)
        assert core.pending_completions(SCOPE) == (rows[0],)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_acknowledgement(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core, 1)
        entered = asyncio.Event()

        class Blocked:
            async def deliver(self, completion: Completion) -> None:
                entered.set()
                await asyncio.Event().wait()

        dispatch = asyncio.create_task(core.dispatch_completion_batch(SCOPE, Blocked()))
        await entered.wait()
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        assert core.pending_completions(SCOPE) == tuple(rows)
        assert not any(e.kind == "completion_delivered" for e in core.audit(SCOPE, rows[0].task_id))
    finally:
        core.close()


@pytest.mark.asyncio
async def test_ack_audit_rollback_is_independent_and_retryable(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core, 2)
        with sqlite3.connect(core.database) as connection:
            connection.execute(
                "CREATE TRIGGER reject_ack BEFORE INSERT ON human_events "
                f"WHEN NEW.task='{rows[0].task_id}' "
                "AND json_extract(NEW.event,'$.kind')='completion_delivered' "
                "BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
            )
        sink = Sink()
        result = await core.dispatch_completion_batch(SCOPE, sink)
        assert (result.delivered, result.failed) == (1, 1)
        assert core.pending_completions(SCOPE) == (rows[0],)
        with sqlite3.connect(core.database) as connection:
            connection.execute("DROP TRIGGER reject_ack")
        assert (await core.dispatch_completion_batch(SCOPE, sink)).delivered == 1
        assert len(sink.accepted) == 2
    finally:
        core.close()


@pytest.mark.asyncio
async def test_concurrent_acknowledgements_commit_one_delivery_event(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    other = service(core.database)
    try:
        rows = completed(core, 1)
        both = asyncio.Event()

        class Coordinated(Sink):
            async def deliver(self, completion: Completion) -> None:
                self.calls.append(completion.task_id)
                if len(self.calls) == 2:
                    both.set()
                await both.wait()

        sink = Coordinated()
        results = await asyncio.gather(
            core.dispatch_completion_batch(SCOPE, sink),
            other.dispatch_completion_batch(SCOPE, sink),
        )
        assert sum(r.delivered for r in results) == 1
        assert sum(r.failed for r in results) == 0
        assert sum(r.attempted for r in results) == 2
        assert (
            len([e for e in core.audit(SCOPE, rows[0].task_id) if e.kind == "completion_delivered"])
            == 1
        )
    finally:
        core.close()
        other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"after_task_id": ""},
        {"after_task_id": 123},
        {"delivery_timeout": 0},
        {"delivery_timeout": -1},
        {"delivery_timeout": 301},
        {"delivery_timeout": True},
        {"delivery_timeout": float("nan")},
        {"delivery_timeout": float("inf")},
        {"delivery_timeout": "1"},
    ],
)
async def test_invalid_bounds_rejected_before_delivery(tmp_path: Path, kwargs: dict) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        completed(core, 1)
        sink = Sink()
        with pytest.raises(ValueError):
            await core.dispatch_completion_batch(SCOPE, sink, **kwargs)
        assert sink.calls == []
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_lost_ack_survives_reopen_with_identical_completion_id(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    row = completed(core, 1)[0]
    sink = Sink(row.task_id)
    try:
        failed = await core.dispatch_completion_batch(SCOPE, sink)
        assert failed.failed == 1
    finally:
        core.close()
    reopened = service(tmp_path / "tasks.db")
    try:
        sink.fail = None
        retried = await reopened.dispatch_completion_batch(SCOPE, sink)
        assert retried.delivered == 1
        assert sink.calls == [row.task_id, row.task_id]
        assert sink.accepted == {row.completion_id}
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_hundred_row_bound_leaves_remainder_for_next_page(tmp_path: Path) -> None:
    core = service(tmp_path / "tasks.db")
    try:
        rows = completed(core, 101)
        sink = Sink()
        first = await core.dispatch_completion_batch(SCOPE, sink, limit=100, delivery_timeout=300)
        assert (first.attempted, first.delivered, first.failed) == (100, 100, 0)
        assert first.next_after_task_id == rows[99].task_id
        assert len(core.pending_completions(SCOPE)) == 1
        last = await core.dispatch_completion_batch(
            SCOPE, sink, limit=100, after_task_id=first.next_after_task_id
        )
        assert last.attempted == last.delivered == 1
    finally:
        core.close()
