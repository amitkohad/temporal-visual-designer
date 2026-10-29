"""Bounded exact-run event recovery rejects inconsistent or private metadata."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import pytest_asyncio

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import (
    DebugEventBatch,
    DebugEventQuery,
    DebugRuntimeEvent,
)
from nexusflow.tests.debugger.test_sessions import Runtime, setup  # noqa: F401


class EventsRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.latest = 3
        self.override = None

    async def events(self, placement, execution_id, run_id, query):
        self.reads.append((placement, execution_id, run_id, query))
        snapshot = replace(await super().snapshot(placement, execution_id, run_id), revision=2)
        earliest = max(1, self.latest - 255) if self.latest else 0
        items = tuple(
            DebugRuntimeEvent(index, "step_started", "start", "running", 0)
            for index in range(
                max(earliest, query.after + 1),
                min(self.latest, max(earliest, query.after + 1) + query.limit - 1) + 1,
            )
        )
        next_after = items[-1].sequence if items else query.after
        batch = DebugEventBatch(
            snapshot,
            items,
            earliest,
            self.latest,
            next_after,
            next_after < self.latest,
            earliest > 0 and query.after < earliest - 1,
        )
        return self.override(batch) if self.override else batch


@pytest_asyncio.fixture
async def events(setup):  # noqa: F811
    scope, service, request, repository, auth, _, readiness = setup
    runtime = EventsRuntime()
    service.runtime = runtime
    session = await service.start(scope, "purchase", request)
    # There must be no hidden runtime-state query, draft, readiness or catalog
    # dependency: the trusted port returns one self-contained metadata batch.
    service.projects = service.readiness = service.catalog = object()
    return scope, service, session, repository, auth, runtime, readiness


async def read(case, **kwargs):
    scope, service, session, *_ = case
    return await service.list_events(scope, "purchase", session.session_id, **kwargs)


@pytest.mark.asyncio
async def test_exclusive_pages_are_exact_run_bound_without_mutation(events):
    scope, service, session, repository, _, runtime, readiness = events
    before = repository.get(scope, "purchase", session.session_id)
    first = await read(events, limit=2)
    assert [event.sequence for event in first.events] == [1, 2]
    assert first.next_after == 2 and first.has_more and not first.gap
    second = await read(events, after=first.next_after, limit=2)
    assert [event.sequence for event in second.events] == [3]
    assert second.next_after == 3 and not second.has_more
    terminal = await read(events, after=3)
    assert terminal.events == () and terminal.next_after == 3
    assert repository.get(scope, "purchase", session.session_id) == before
    assert runtime.controls == [] and len(runtime.starts) == readiness.calls == 1
    assert runtime.reads[0] == (
        service.placement,
        session.execution_id,
        session.temporal_run_id,
        DebugEventQuery(0, 2),
    )
    assert "private" not in first.model_dump_json() and "snapshot" not in first.model_dump_json()


@pytest.mark.asyncio
async def test_retention_gap_restarts_at_oldest_available_sequence(events):
    runtime = events[5]
    runtime.latest = 300
    first = await read(events, after=0, limit=25)
    assert first.earliest_sequence == 45 and first.latest_sequence == 300
    assert first.gap and first.next_after == 69 and first.has_more
    resumed = await read(events, after=first.next_after)
    assert resumed.events[0].sequence == 70 and not resumed.gap
    assert len(first.model_dump_json().encode()) < 16384


@pytest.mark.asyncio
async def test_initial_empty_window_and_terminal_none_metadata(events):
    runtime = events[5]
    runtime.latest = 0
    page = await read(events)
    assert (
        page.events == () and page.next_after == page.latest_sequence == page.earliest_sequence == 0
    )
    assert not page.gap and not page.has_more
    runtime.latest = 1
    runtime.override = lambda batch: replace(
        batch, events=(DebugRuntimeEvent(1, "completed", None, "completed", 2),)
    )
    assert (await read(events)).events[0].node_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "after,limit", [(True, 1), (-1, 1), (2**63, 1), (0, True), (0, 0), (0, 26), (0, "1")]
)
async def test_invalid_query_fails_before_runtime(events, after, limit):
    with pytest.raises(DebugError) as error:
        await read(events, after=after, limit=limit)
    assert error.value.code == "invalid_request"
    assert events[5].reads == []


@pytest.mark.asyncio
async def test_future_cursor_is_invalid_not_a_fabricated_empty_page(events):
    with pytest.raises(DebugError) as error:
        await read(events, after=4)
    assert error.value.code == "invalid_request"


def altered_event(batch, **kwargs):
    return replace(batch, events=(replace(batch.events[0], **kwargs), *batch.events[1:]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        lambda batch: None,
        lambda batch: replace(batch, events=list(batch.events)),
        lambda batch: replace(batch, events=batch.events[:-1]),
        lambda batch: replace(batch, events=tuple(reversed(batch.events))),
        lambda batch: replace(batch, events=(batch.events[0], batch.events[0], batch.events[2])),
        lambda batch: replace(batch, earliest_sequence=2),
        lambda batch: replace(batch, earliest_sequence=0),
        lambda batch: replace(batch, latest_sequence=True),
        lambda batch: replace(batch, latest_sequence=2**63),
        lambda batch: replace(batch, next_after=2),
        lambda batch: replace(batch, next_after=True),
        lambda batch: replace(batch, has_more=True),
        lambda batch: replace(batch, has_more=0),
        lambda batch: replace(batch, gap=True),
        lambda batch: replace(batch, gap=0),
        lambda batch: replace(batch, snapshot=replace(batch.snapshot, revision=True)),
        lambda batch: replace(batch, snapshot=replace(batch.snapshot, revision=-1)),
        lambda batch: replace(batch, snapshot=replace(batch.snapshot, release_id="other")),
        lambda batch: replace(
            batch, snapshot=replace(batch.snapshot, definition_digest="sha256:" + "a" * 64)
        ),
        lambda batch: replace(batch, snapshot=replace(batch.snapshot, runtime_version="other")),
        lambda batch: altered_event(batch, node_id="private-dynamic-node"),
        lambda batch: altered_event(batch, node_id=None),
        lambda batch: altered_event(batch, sequence=True),
        lambda batch: altered_event(batch, sequence=2),
        lambda batch: altered_event(batch, revision=True),
        lambda batch: altered_event(batch, revision=3),
        lambda batch: altered_event(batch, kind="private exception message"),
        lambda batch: altered_event(batch, state="private state payload"),
        lambda batch: replace(
            batch, events=(replace(batch.events[0], revision=2), *batch.events[1:])
        ),
    ],
    ids=lambda change: None,
)
async def test_malformed_pages_fail_closed_without_provider_text(events, change):
    events[5].override = change
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "unavailable"
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_gap_and_retention_window_must_be_exact(events):
    events[5].latest = 300
    events[5].override = lambda batch: replace(batch, earliest_sequence=46)
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "unavailable"
    events[5].override = lambda batch: replace(batch, gap=False)
    with pytest.raises(DebugError):
        await read(events)


@pytest.mark.asyncio
async def test_page_cannot_exceed_requested_limit(events):
    events[5].override = lambda batch: replace(
        batch, events=(*batch.events, DebugRuntimeEvent(2, "paused", "start", "paused", 0))
    )
    with pytest.raises(DebugError) as error:
        await read(events, limit=1)
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_legacy_runtime_and_safe_provider_failures(events):
    runtime = events[5]
    events[1].runtime = object()
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "unavailable"
    events[1].runtime = runtime

    def fail(batch):
        raise OSError("private provider error")

    runtime.override = fail
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "unavailable" and "private" not in str(error.value)


@pytest.mark.asyncio
async def test_fresh_owner_revocation_and_placement_guards(events):
    auth = events[4]
    auth.actor = "other"
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "forbidden"
    auth.actor, auth.denied = "alice", True
    with pytest.raises(PermissionError):
        await read(events)
    auth.denied = False
    events[1].placement = replace(events[1].placement, task_queue="moved")
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "conflict"
    assert events[5].reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_non_dev_rejected_before_provider(events, environment):
    with pytest.raises(DebugError) as error:
        await events[1].list_events(
            Scope(organization_id="acme", environment_id=environment),
            "purchase",
            events[2].session_id,
        )
    assert error.value.code == "forbidden" and events[5].reads == []


@pytest.mark.asyncio
async def test_pending_dispatch_is_rejected_before_runtime(events):
    scope, service, session, repository, _, runtime, _ = events
    record = repository.get(scope, "purchase", session.session_id)
    repository.get = Mock(
        return_value=replace(
            record,
            session=record.session.model_copy(
                update={"state": "dispatch_pending", "temporal_run_id": None}
            ),
        )
    )
    with pytest.raises(DebugError) as error:
        await read(events)
    assert error.value.code == "conflict" and runtime.reads == []
