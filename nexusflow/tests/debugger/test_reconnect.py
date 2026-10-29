"""Atomic reconnect metadata rejects mixed, private or impossible baselines."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
import pytest_asyncio

from nexusflow.modules.designer_debugger import service as service_module
from nexusflow.modules.designer_debugger.reconnect_types import MAX_RECONNECT_BYTES
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import DebugReconnectState, Step
from nexusflow.tests.debugger.test_sessions import Runtime, setup  # noqa: F401


class ReconnectRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.override = None
        self.error = None

    async def reconnect(self, placement, execution_id, run_id):
        self.reads.append((placement, execution_id, run_id))
        if self.error:
            raise self.error
        snapshot = replace(
            await Runtime.snapshot(self, placement, execution_id, run_id),
            node_id="work",
            completed_nodes=("start",),
            revision=1,
        )
        result = DebugReconnectState(snapshot, ("work",), 1, 5)
        return self.override(result) if self.override else result

    async def snapshot(self, *args):
        raise AssertionError("Reconnect must not split the runtime snapshot query")

    async def events(self, *args):
        raise AssertionError("Reconnect must not split the runtime event query")

    async def breakpoints(self, *args):
        raise AssertionError("Reconnect must not split the runtime breakpoint query")


@pytest_asyncio.fixture
async def reconnect_case(setup):  # noqa: F811
    scope, service, request, repository, auth, _, readiness = setup
    content = json.loads(service.projects.revision(scope, "purchase", 1).content_json)
    workflow = content["workflows"][0]
    workflow["nodes"].insert(
        1,
        {
            "node_id": "work",
            "kind": "wait",
            "named_ports": {"inputs": ["in"], "outputs": ["out"]},
            "input_mapping": {"seconds": 0},
        },
    )
    workflow["edges"] = [
        {
            "source_node_id": source,
            "source_port": "out",
            "target_node_id": target,
            "target_port": "in",
        }
        for source, target in (("start", "work"), ("work", "finish"))
    ]
    service.projects.save(scope, "purchase", 1, json.dumps(content), "Wait", "alice")
    runtime = ReconnectRuntime()
    service.runtime = runtime
    session = await service.start(
        scope, "purchase", request.model_copy(update={"source_revision": 2})
    )
    service.projects = service.readiness = service.catalog = object()
    return scope, service, session, repository, auth, runtime, readiness


async def read(case):
    scope, service, session, *_ = case
    return await service.reconnect(scope, "purchase", session.session_id)


@pytest.mark.asyncio
async def test_one_exact_run_query_returns_snapshot_breakpoints_and_matching_cursor(reconnect_case):
    scope, service, session, repository, _, runtime, readiness = reconnect_case
    before = repository.get(scope, "purchase", session.session_id)
    result = await read(reconnect_case)
    assert result.session_id == session.session_id
    assert result.snapshot.node_id == "work" and result.snapshot.revision == 1
    assert result.snapshot.completed_nodes == ("start",)
    assert result.breakpoints == ("work",)
    assert result.earliest_sequence == 1 and result.next_after == result.latest_sequence == 5
    assert runtime.reads == [(service.placement, session.execution_id, session.temporal_run_id)]
    assert repository.get(scope, "purchase", session.session_id) == before
    assert len(runtime.starts) == readiness.calls == 1 and runtime.controls == []
    encoded = result.model_dump_json()
    assert "private" not in encoded and '"input"' not in encoded and '"output"' not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        "paused",
        "running",
        "waiting_timer",
        "waiting_approval",
        "creating_task",
        "cancelling",
        "cancelled",
        "failed",
    ],
)
async def test_planned_current_position_is_readable_without_changing_execution(
    reconnect_case, state
):
    runtime = reconnect_case[5]
    runtime.override = lambda result: replace(
        result, snapshot=replace(result.snapshot, state=state)
    )
    assert (await read(reconnect_case)).snapshot.state == state
    assert runtime.controls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_node", ["", "start"])
async def test_empty_starting_and_completed_baselines_and_retention_cursor(
    reconnect_case, initial_node
):
    runtime = reconnect_case[5]
    runtime.override = lambda result: replace(
        result,
        snapshot=replace(
            result.snapshot, node_id=initial_node, state="starting", completed_nodes=(), revision=0
        ),
        earliest_sequence=0,
        latest_sequence=0,
    )
    initial = await read(reconnect_case)
    assert initial.next_after == initial.earliest_sequence == initial.latest_sequence == 0
    runtime.override = lambda result: replace(
        result,
        snapshot=replace(
            result.snapshot,
            node_id="finish",
            state="completed",
            completed_nodes=("start", "work", "finish"),
        ),
        earliest_sequence=45,
        latest_sequence=300,
    )
    terminal = await read(reconnect_case)
    assert terminal.next_after == 300 and terminal.earliest_sequence == 45
    assert terminal.snapshot.state == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["paused-zero", "completed-zero", "starting-nonzero"])
async def test_event_cursor_and_runtime_phase_cannot_describe_different_instants(
    reconnect_case, phase
):
    runtime = reconnect_case[5]

    def mismatch(result):
        if phase == "paused-zero":
            snapshot = replace(result.snapshot, revision=0)
        elif phase == "completed-zero":
            snapshot = replace(
                result.snapshot,
                node_id="finish",
                state="completed",
                completed_nodes=("start", "work", "finish"),
                revision=0,
            )
        else:
            snapshot = replace(
                result.snapshot, node_id="", state="starting", completed_nodes=(), revision=0
            )
        return replace(
            result,
            snapshot=snapshot,
            earliest_sequence=1 if phase == "starting-nonzero" else 0,
            latest_sequence=5 if phase == "starting-nonzero" else 0,
        )

    runtime.override = mismatch
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable" and len(runtime.reads) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"release_id": "private-release"},
        {"definition_digest": "private-digest"},
        {"runtime_version": "private-runtime"},
        {"state": "private-error-value"},
        {"state": 5},
        {"revision": True},
        {"revision": -1},
        {"revision": 2**63},
        {"revision": 6},
        {"node_id": "unknown-private-node"},
        {"node_id": ""},
        {"node_id": True},
        {"completed_nodes": ["start"]},
        {"completed_nodes": ("work",)},
        {"completed_nodes": ("start", "start")},
        {"completed_nodes": ("start", "work")},
        {"completed_nodes": ("start", "work", "finish", "extra")},
        {"state": "starting"},
        {"state": "completed"},
        {"state": "completed", "completed_nodes": ("start", "work", "finish")},
    ],
)
async def test_impossible_or_mixed_snapshot_is_rejected_without_echo(reconnect_case, change):
    runtime = reconnect_case[5]
    runtime.override = lambda result: replace(result, snapshot=replace(result.snapshot, **change))
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable"
    assert "private" not in str(error.value)
    assert len(runtime.reads) == 1 and runtime.controls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"earliest_sequence": True},
        {"latest_sequence": True},
        {"earliest_sequence": -1},
        {"latest_sequence": -1},
        {"latest_sequence": 2**63},
        {"earliest_sequence": 2},
        {"earliest_sequence": 0},
        {"earliest_sequence": 0, "latest_sequence": 0},
        {"earliest_sequence": 1, "latest_sequence": 300},
        {"breakpoints": ["work"]},
        {"breakpoints": ("work", "work")},
        {"breakpoints": ("start",)},
        {"breakpoints": ("finish",)},
        {"breakpoints": ("unknown-private-node",)},
        {"breakpoints": (True,)},
    ],
)
async def test_invalid_cursor_window_or_targets_are_unavailable(reconnect_case, change):
    runtime = reconnect_case[5]
    runtime.override = lambda result: replace(result, **change)
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable" and "private" not in str(error.value)


@pytest.mark.asyncio
async def test_changed_runtime_metadata_does_not_mutate_previously_returned_baseline(
    reconnect_case,
):
    runtime = reconnect_case[5]
    first = await read(reconnect_case)
    original = first.model_dump_json()
    runtime.override = lambda result: replace(result, breakpoints=(), latest_sequence=6)
    second = await read(reconnect_case)
    assert second.next_after == 6 and second.breakpoints == ()
    assert first.model_dump_json() == original and first.next_after == 5


@pytest.mark.asyncio
async def test_legacy_provider_and_safe_failures(reconnect_case):
    runtime = reconnect_case[5]
    runtime.override = lambda result: {"private": "must-not-echo"}
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable" and "private" not in str(error.value)
    runtime.override = None
    runtime.error = OSError("private-provider-error")
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable" and "private" not in str(error.value)
    reconnect_case[1].runtime = object()
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable"


@pytest.mark.asyncio
async def test_owner_revocation_pending_dispatch_and_non_dev_before_runtime(reconnect_case):
    scope, service, session, repository, auth, runtime, _ = reconnect_case
    auth.actor = "other"
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "forbidden"
    auth.actor, auth.denied = "alice", True
    with pytest.raises(PermissionError):
        await read(reconnect_case)
    auth.denied = False
    for environment in ("uat", "prod"):
        with pytest.raises(DebugError) as error:
            await service.reconnect(
                scope.model_copy(update={"environment_id": environment}),
                "purchase",
                session.session_id,
            )
        assert error.value.code == "forbidden"
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
        await read(reconnect_case)
    assert error.value.code == "conflict" and runtime.reads == []


@pytest.mark.asyncio
async def test_long_valid_plan_and_breakpoints_fit_dedicated_metadata_budget(reconnect_case):
    _, service, session, repository, _, runtime, _ = reconnect_case
    record = repository.get(reconnect_case[0], "purchase", session.session_id)
    names = tuple(f"n{index:03}_" + "x" * 123 for index in range(100))
    steps = tuple(
        Step(name, "start" if index == 0 else "end" if index == 99 else "wait")
        for index, name in enumerate(names)
    )
    repository.get = Mock(return_value=replace(record, plan=replace(record.plan, steps=steps)))
    runtime.override = lambda result: replace(
        result,
        snapshot=replace(
            result.snapshot, node_id=names[-1], state="completed", completed_nodes=names
        ),
        breakpoints=names[1:-1],
        earliest_sequence=245,
        latest_sequence=500,
    )
    result = await read(reconnect_case)
    assert 16384 < len(result.model_dump_json().encode()) <= MAX_RECONNECT_BYTES
    assert len(result.snapshot.completed_nodes) == 100 and len(result.breakpoints) == 98


@pytest.mark.asyncio
async def test_complete_packet_limit_is_enforced(reconnect_case, monkeypatch):
    result = await read(reconnect_case)
    monkeypatch.setattr(
        service_module, "MAX_RECONNECT_BYTES", len(result.model_dump_json().encode()) - 1
    )
    with pytest.raises(DebugError) as error:
        await read(reconnect_case)
    assert error.value.code == "unavailable"
