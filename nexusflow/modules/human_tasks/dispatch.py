"""Explicitly owned, bounded polling for durable human-task completions."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import AsyncCompletionSink, CompletionBatch


def _positive_duration(value: float, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value <= 300
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be finite, positive and at most 300 seconds")


class CompletionDispatcher:
    """One explicitly scoped polling owner; never starts implicitly with the API.

    Failed deliveries remain in the service outbox. The cursor scans past them
    through bounded batches and wraps after an empty batch so later work cannot
    be starved by a repeatedly failing completion.
    """

    def __init__(
        self,
        service: TaskService,
        scope: Scope,
        sink: AsyncCompletionSink,
        *,
        batch_size: int = 25,
        poll_interval: float = 1.0,
        delivery_timeout: float = 10.0,
    ) -> None:
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be an integer between 1 and 100")
        _positive_duration(poll_interval, "poll_interval")
        _positive_duration(delivery_timeout, "delivery_timeout")
        self.service = service
        self.scope = scope
        self.sink = sink
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.delivery_timeout = delivery_timeout
        self.last_batch: CompletionBatch | None = None
        self.last_error_code: str | None = None
        self._after_task_id: str | None = None
        self._running = False
        self._dispatching = False
        self._context_active = False
        self._context_task: asyncio.Task[None] | None = None

    async def _dispatch_once(self) -> CompletionBatch:
        if self._dispatching:
            raise RuntimeError("completion dispatcher is already dispatching")
        self._dispatching = True
        try:
            batch = await self.service.dispatch_completion_batch(
                self.scope,
                self.sink,
                limit=self.batch_size,
                after_task_id=self._after_task_id,
                delivery_timeout=self.delivery_timeout,
            )
            self._after_task_id = batch.next_after_task_id
            self.last_batch = batch
            self.last_error_code = None
            return batch
        except Exception:
            self.last_error_code = "dispatch_unavailable"
            raise
        finally:
            self._dispatching = False

    async def run_once(self) -> CompletionBatch:
        """Dispatch one batch; concurrent manual calls and polling are rejected."""
        if self._running or self._context_active:
            raise RuntimeError("completion dispatcher is already running")
        return await self._dispatch_once()

    async def run(self) -> None:
        """Poll immediately, then sleep after every batch or recoverable failure."""
        if (
            self._running
            or self._dispatching
            or (self._context_active and asyncio.current_task() is not self._context_task)
        ):
            raise RuntimeError("completion dispatcher is already running")
        self._running = True
        try:
            while True:
                try:
                    await self._dispatch_once()
                except Exception:
                    # Deliberately retain no exception details or payloads.
                    self.last_error_code = "dispatch_unavailable"
                await asyncio.sleep(self.poll_interval)
        finally:
            self._running = False


@asynccontextmanager
async def run_completion_dispatcher(
    dispatcher: CompletionDispatcher,
) -> AsyncIterator[CompletionDispatcher]:
    """Cancel and join the owned loop before the caller closes its resources."""
    if dispatcher._context_active or dispatcher._running or dispatcher._dispatching:
        raise RuntimeError("completion dispatcher already has an owner")
    dispatcher._context_active = True
    task = asyncio.create_task(dispatcher.run())
    dispatcher._context_task = task
    try:
        yield dispatcher
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            dispatcher._context_active = False
            dispatcher._context_task = None
