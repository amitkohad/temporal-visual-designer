"""Worker lifecycle tests with a fake batch port; no hosted-provider claims."""

import asyncio
from typing import Any, cast

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.dispatch import CompletionDispatcher, run_completion_dispatcher
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import AsyncCompletionSink, CompletionBatch

SCOPE = Scope(organization_id="org-a", environment_id="dev")


class BatchPort:
    def __init__(self, results: list[CompletionBatch | Exception]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []
        self.entered = asyncio.Event()
        self.block: asyncio.Event | None = None
        self.cancelled = False

    async def dispatch_completion_batch(
        self, scope: Scope, sink: object, **kwargs: Any
    ) -> CompletionBatch:
        assert scope == SCOPE
        self.calls.append(kwargs)
        self.entered.set()
        if self.block is not None:
            try:
                await self.block.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        result = self.results.pop(0) if self.results else batch()
        if isinstance(result, Exception):
            raise result
        return result


def batch(cursor: str | None = None, failed: int = 0) -> CompletionBatch:
    return CompletionBatch(
        attempted=1 if cursor else 0, delivered=0, failed=failed, next_after_task_id=cursor
    )


def worker(port: BatchPort, **kwargs: Any) -> CompletionDispatcher:
    return CompletionDispatcher(
        cast(TaskService, port), SCOPE, cast(AsyncCompletionSink, object()), **kwargs
    )


@pytest.mark.parametrize("value", [True, False, 0, 101, 1.0, "25", None])
def test_batch_validation(value: Any) -> None:
    with pytest.raises(ValueError):
        worker(BatchPort([]), batch_size=value)


@pytest.mark.parametrize("name", ["poll_interval", "delivery_timeout"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 301, float("inf"), float("nan"), "1", None])
def test_duration_validation(name: str, value: Any) -> None:
    with pytest.raises(ValueError):
        worker(BatchPort([]), **{name: value})


@pytest.mark.asyncio
async def test_backlog_failure_cursor_and_wrap() -> None:
    port = BatchPort([batch("task-a", 1), batch("task-b"), batch(), batch("task-a")])
    dispatcher = worker(port, batch_size=2, delivery_timeout=3)
    for _ in range(4):
        await dispatcher.run_once()
    assert [call["after_task_id"] for call in port.calls] == [None, "task-a", "task-b", None]
    assert all(call["limit"] == 2 and call["delivery_timeout"] == 3 for call in port.calls)
    assert dispatcher.last_batch == batch("task-a")
    assert dispatcher.last_error_code is None


@pytest.mark.asyncio
async def test_failure_preserves_cursor_and_redacts_then_recovers() -> None:
    port = BatchPort([batch("task-a"), RuntimeError("secret payload"), batch("task-b")])
    dispatcher = worker(port)
    await dispatcher.run_once()
    with pytest.raises(RuntimeError):
        await dispatcher.run_once()
    assert dispatcher.last_error_code == "dispatch_unavailable"
    assert dispatcher.last_batch == batch("task-a")
    await dispatcher.run_once()
    assert dispatcher.last_error_code is None
    assert [call["after_task_id"] for call in port.calls] == [None, "task-a", "task-a"]


@pytest.mark.asyncio
async def test_owned_lifecycle_cancels_inflight_before_exit() -> None:
    port = BatchPort([])
    port.block = asyncio.Event()
    dispatcher = worker(port)
    async with run_completion_dispatcher(dispatcher) as running:
        assert running is dispatcher
        await port.entered.wait()
        with pytest.raises(RuntimeError):
            await dispatcher.run_once()
        with pytest.raises(RuntimeError):
            await dispatcher.run()
        with pytest.raises(RuntimeError):
            async with run_completion_dispatcher(dispatcher):
                pytest.fail("duplicate context entered")
    assert port.cancelled
    port.block = None
    assert await dispatcher.run_once() == batch()


@pytest.mark.asyncio
async def test_duplicate_context_rejected_before_loop_scheduled() -> None:
    dispatcher = worker(BatchPort([]))
    async with run_completion_dispatcher(dispatcher):
        with pytest.raises(RuntimeError):
            await dispatcher.run_once()
        with pytest.raises(RuntimeError):
            await dispatcher.run()
        with pytest.raises(RuntimeError):
            async with run_completion_dispatcher(dispatcher):
                pytest.fail("duplicate context entered")


@pytest.mark.asyncio
async def test_manual_overlap_and_cancellation_release_guard() -> None:
    port = BatchPort([])
    port.block = asyncio.Event()
    dispatcher = worker(port)
    task = asyncio.create_task(dispatcher.run_once())
    await port.entered.wait()
    with pytest.raises(RuntimeError):
        await dispatcher.run_once()
    with pytest.raises(RuntimeError):
        await dispatcher.run()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatcher.last_error_code is None
    port.block = None
    await dispatcher.run_once()


@pytest.mark.asyncio
async def test_loop_paces_failure_and_empty_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    port = BatchPort([RuntimeError("sensitive"), batch(), batch("task-a", 1)])
    dispatcher = worker(port, poll_interval=2)
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 1:
            assert dispatcher.last_error_code == "dispatch_unavailable"
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run()
    assert sleeps == [2, 2, 2]
    assert len(port.calls) == 3
    assert dispatcher.last_batch == batch("task-a", 1)
    assert dispatcher.last_error_code is None


@pytest.mark.asyncio
async def test_context_exception_stops_owned_loop() -> None:
    port = BatchPort([])
    port.block = asyncio.Event()
    dispatcher = worker(port)
    with pytest.raises(ValueError, match="caller failure"):
        async with run_completion_dispatcher(dispatcher):
            await port.entered.wait()
            raise ValueError("caller failure")
    assert port.cancelled
