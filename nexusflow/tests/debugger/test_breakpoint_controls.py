"""Shared command identity, pinned breakpoint state and durable replacement receipts."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
import pytest_asyncio
from pydantic import ValidationError

from nexusflow.modules.designer_debugger.breakpoint_types import DebugBreakpointControl
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugControl, DebugError
from nexusflow.modules.durable_runtime.types import CommandReceipt, DebugBreakpointState
from nexusflow.tests.debugger.test_sessions import Runtime, setup  # noqa: F401


class BreakpointRuntime(Runtime):
    def __init__(self):
        super().__init__()
        self.active = ()
        self.revision = 0
        self.state = "paused"
        self.reads = []
        self.replacements = []
        self.accepted = {}
        self.unavailable = False

    async def snapshot(self, placement, execution_id, run_id):
        self.reads.append((placement, execution_id, run_id))
        if self.unavailable:
            raise OSError("private provider detail")
        return replace(
            await super().snapshot(placement, execution_id, run_id),
            node_id="first",
            state=self.state,
            revision=self.revision,
        )

    async def breakpoints(self, placement, execution_id, run_id):
        return DebugBreakpointState(
            await self.snapshot(placement, execution_id, run_id), self.active
        )

    async def replace_breakpoints(self, placement, execution_id, run_id, command):
        self.replacements.append((placement, execution_id, run_id, command))
        if self.unavailable:
            raise OSError("private provider detail")
        if command.command_id in self.accepted:
            return self.accepted[command.command_id]
        if (
            self.state != "paused"
            or command.node_id != "first"
            or command.expected_revision != self.revision
        ):
            raise DebugError("conflict", "Paused node and revision required")
        self.active = command.breakpoints
        self.revision += 1
        receipt = CommandReceipt(command.command_id, self.revision)
        self.accepted[command.command_id] = receipt
        return receipt


@pytest_asyncio.fixture
async def breakpoints(setup):  # noqa: F811
    scope, service, request, repository, auth, _, readiness = setup
    content = json.loads(service.projects.revision(scope, "purchase", 1).content_json)
    workflow = content["workflows"][0]
    workflow["nodes"] = [
        workflow["nodes"][0],
        *[
            {
                "node_id": node,
                "kind": "wait",
                "named_ports": {"inputs": ["in"], "outputs": ["out"]},
                "input_mapping": {"seconds": 0},
            }
            for node in ("first", "second")
        ],
        workflow["nodes"][-1],
    ]
    names = [node["node_id"] for node in workflow["nodes"]]
    workflow["edges"] = [
        {"source_node_id": left, "source_port": "out", "target_node_id": right, "target_port": "in"}
        for left, right in zip(names, names[1:], strict=False)
    ]
    service.projects.save(scope, "purchase", 1, json.dumps(content), "Waits", "alice")
    runtime = BreakpointRuntime()
    service.runtime = runtime
    session = await service.start(
        scope, "purchase", request.model_copy(update={"source_revision": 2})
    )
    return scope, service, session, repository, auth, runtime, readiness


def command(**changes):
    return DebugBreakpointControl(
        command_id="breakpoints",
        node_id="first",
        breakpoints=("second",),
        expected_revision=0,
        **changes,
    )


async def replace_set(case, request=None):
    scope, service, session, *_ = case
    return await service.replace_breakpoints(
        scope, "purchase", session.session_id, request or command()
    )


async def read_set(case):
    scope, service, session, *_ = case
    return await service.get_breakpoints(scope, "purchase", session.session_id)


@pytest.mark.asyncio
async def test_replacement_changes_overlay_without_plan_controls_or_readiness(breakpoints):
    _, service, session, repository, _, runtime, readiness = breakpoints
    before = repository.get(breakpoints[0], "purchase", session.session_id)
    assert (await read_set(breakpoints)).breakpoints == ()
    receipt = await replace_set(breakpoints)
    state = await read_set(breakpoints)
    assert state.breakpoints == ("second",) and state.revision == receipt.revision == 1
    assert runtime.state == "paused" and runtime.controls == []
    assert repository.get(breakpoints[0], "purchase", session.session_id) == before
    assert readiness.calls == 1
    assert runtime.replacements[0][0:3] == (service.placement, session.execution_id, "run-1")


@pytest.mark.asyncio
async def test_saved_receipt_reopens_and_retries_without_runtime_or_mutable_providers(
    breakpoints, tmp_path
):
    _, service, _, _, _, runtime, _ = breakpoints
    receipt = await replace_set(breakpoints)
    runtime.unavailable = True
    service.projects, service.readiness = object(), object()
    reopened = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        service.repository = reopened
        assert await replace_set(breakpoints) == receipt
        assert len(runtime.replacements) == 1
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_pending_identity_conflicts_with_changed_body_and_normal_controls(breakpoints):
    scope, service, session, repository, _, runtime, _ = breakpoints
    runtime.unavailable = True
    with pytest.raises(DebugError) as failure:
        await replace_set(breakpoints)
    assert failure.value.code == "unavailable"
    for changed in (
        command().model_copy(update={"breakpoints": ()}),
        command().model_copy(update={"node_id": "second"}),
    ):
        with pytest.raises(DebugError) as failure:
            await replace_set(breakpoints, changed)
        assert failure.value.code == "conflict"
    control = DebugControl(
        command_id="breakpoints", node_id="first", action="continue", expected_revision=0
    )
    with pytest.raises(DebugError) as failure:
        await service.control(scope, "purchase", session.session_id, control)
    assert failure.value.code == "conflict"
    assert repository.claim_command(scope, "purchase", session.session_id, command()) is None
    assert runtime.replacements == []


@pytest.mark.asyncio
async def test_existing_normal_command_json_is_compatible_and_blocks_cross_kind(
    breakpoints, tmp_path
):
    scope, service, session, repository, _, _, _ = breakpoints
    control = DebugControl(
        command_id="legacy", node_id="first", action="step_over", expected_revision=0
    )
    assert repository.claim_command(scope, "purchase", session.session_id, control) is None
    repository.acknowledge_command(
        scope, "purchase", session.session_id, control, CommandReceipt("legacy", 1)
    )
    with SQLiteDebugSessionRepositoryContext(tmp_path / "debug.db") as reopened:
        assert reopened.claim_command(
            scope, "purchase", session.session_id, control
        ) == CommandReceipt("legacy", 1)
    with pytest.raises(DebugError) as failure:
        await replace_set(breakpoints, command().model_copy(update={"command_id": "legacy"}))
    assert failure.value.code == "conflict"


class SQLiteDebugSessionRepositoryContext(SQLiteDebugSessionRepository):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@pytest.mark.asyncio
async def test_both_command_kinds_share_one_thousand_reservation_quota(breakpoints):
    scope, _, session, repository, _, _, _ = breakpoints
    await replace_set(breakpoints)
    repository.connection.executemany(
        "INSERT INTO debug_commands VALUES(?,?,?,?,?,?,NULL)",
        [
            (
                scope.organization_id,
                scope.environment_id,
                "purchase",
                session.session_id,
                f"legacy-{index}",
                DebugControl(
                    command_id=f"legacy-{index}",
                    node_id="first",
                    action="continue",
                    expected_revision=1,
                ).model_dump_json(),
            )
            for index in range(999)
        ],
    )
    repository.connection.commit()
    for candidate in (
        command().model_copy(update={"command_id": "new"}),
        DebugControl(command_id="new", node_id="first", action="continue", expected_revision=1),
    ):
        with pytest.raises(DebugError) as failure:
            repository.claim_command(scope, "purchase", session.session_id, candidate)
        assert failure.value.code == "limit_exceeded"
    assert await replace_set(breakpoints) == CommandReceipt("breakpoints", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "denied", "placement"])
async def test_fresh_guards_precede_runtime_and_saved_receipt(breakpoints, change):
    _, service, _, _, auth, runtime, _ = breakpoints
    await replace_set(breakpoints)
    before = len(runtime.reads)
    if change == "owner":
        auth.actor = "other"
    elif change == "denied":
        auth.denied = True
    else:
        service.placement = replace(service.placement, task_queue="moved")
    with pytest.raises((DebugError, PermissionError)):
        await replace_set(breakpoints)
    with pytest.raises((DebugError, PermissionError)):
        await read_set(breakpoints)
    assert len(runtime.reads) == before and len(runtime.replacements) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("targets", [("start",), ("finish",), ("missing",)])
async def test_nonexecutable_or_unknown_targets_are_rejected_without_runtime(breakpoints, targets):
    *_, runtime, _ = breakpoints
    with pytest.raises(DebugError) as failure:
        await replace_set(breakpoints, command().model_copy(update={"breakpoints": targets}))
    assert failure.value.code == "invalid_request"
    assert runtime.reads == [] and runtime.replacements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["release", "targets", "order", "revision", "shape"])
async def test_read_rejects_untrusted_runtime_state(breakpoints, change):
    _, service, _, _, _, runtime, _ = breakpoints
    snapshot = await runtime.snapshot(service.placement, "unused", "run-1")
    state = DebugBreakpointState(snapshot, ("second",))
    if change == "release":
        state = replace(state, snapshot=replace(snapshot, release_id="wrong"))
    elif change == "targets":
        state = replace(state, breakpoints=("start",))
    elif change == "order":
        state = replace(state, breakpoints=("second", "first"))
    elif change == "revision":
        state = replace(state, snapshot=replace(snapshot, revision=True))
    else:
        state = None

    async def invalid(*args):
        return state

    runtime.breakpoints = invalid
    with pytest.raises(DebugError) as failure:
        await read_set(breakpoints)
    assert failure.value.code == "unavailable"


@pytest.mark.asyncio
async def test_missing_legacy_runtime_ports_are_safe_unavailable(breakpoints):
    _, service, _, _, _, _, _ = breakpoints
    service.runtime = object()
    for operation in (read_set, replace_set):
        with pytest.raises(DebugError) as failure:
            await operation(breakpoints)
        assert failure.value.code == "unavailable"


@pytest.mark.asyncio
async def test_lost_local_ack_recovers_same_remote_update_without_double_effect(breakpoints):
    _, _, _, repository, _, runtime, _ = breakpoints
    acknowledge = repository.acknowledge_command

    def unavailable(*args):
        raise OSError("private acknowledgement failure")

    repository.acknowledge_command = unavailable
    with pytest.raises(DebugError):
        await replace_set(breakpoints)
    assert runtime.revision == 1
    repository.acknowledge_command = acknowledge
    assert await replace_set(breakpoints) == CommandReceipt("breakpoints", 1)
    assert runtime.revision == 1 and len(runtime.replacements) == 2


def test_breakpoint_request_canonicalizes_order_but_rejects_duplicates():
    value = DebugBreakpointControl(
        command_id="set", node_id="first", breakpoints=["second", "first"], expected_revision=0
    )
    assert value.breakpoints == ("first", "second")
    with pytest.raises(ValidationError):
        DebugBreakpointControl(
            command_id="set", node_id="first", breakpoints=["first", "first"], expected_revision=0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [0, -1, True, 2**63])
async def test_corrupt_cached_breakpoint_receipts_fail_without_runtime(breakpoints, revision):
    _, _, _, repository, _, runtime, _ = breakpoints
    await replace_set(breakpoints)
    repository.connection.execute(
        "UPDATE debug_commands SET receipt=?",
        (json.dumps({"command_id": "breakpoints", "revision": revision}),),
    )
    repository.connection.commit()
    runtime.unavailable = True
    previous_reads = len(runtime.reads)
    with pytest.raises(DebugError) as failure:
        await replace_set(breakpoints)
    assert failure.value.code == "unavailable"
    assert len(runtime.reads) == previous_reads and len(runtime.replacements) == 1


@pytest.mark.asyncio
async def test_replacement_checks_runtime_binding_before_remote_update(breakpoints):
    *_, runtime, _ = breakpoints
    runtime.wrong_binding = True
    with pytest.raises(DebugError) as failure:
        await replace_set(breakpoints)
    assert failure.value.code == "unavailable" and runtime.replacements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["uat", "prod"])
async def test_non_dev_rejected_before_saved_session_provider_access(breakpoints, environment):
    scope, service, session, _, _, runtime, _ = breakpoints
    target = scope.model_copy(update={"environment_id": environment})
    with pytest.raises(DebugError) as failure:
        await service.get_breakpoints(target, "purchase", session.session_id)
    assert failure.value.code == "forbidden"
    with pytest.raises(DebugError) as failure:
        await service.replace_breakpoints(target, "purchase", session.session_id, command())
    assert failure.value.code == "forbidden" and runtime.reads == []


@pytest.mark.asyncio
async def test_corrupt_session_cannot_reach_breakpoint_runtime(breakpoints):
    scope, _, session, repository, _, runtime, _ = breakpoints
    repository.connection.execute("UPDATE debug_sessions SET checksum='bad'")
    repository.connection.commit()
    # Corrupted session state is never used to reach either runtime endpoint.
    for operation in (read_set, replace_set):
        with pytest.raises(DebugError) as failure:
            await operation(breakpoints)
        assert failure.value.code == "unavailable"
    assert runtime.reads == [] and runtime.replacements == []


@pytest.mark.asyncio
async def test_pending_dispatch_cannot_read_or_replace_breakpoints(breakpoints):
    scope, _, session, repository, _, runtime, _ = breakpoints
    record = repository.get(scope, "purchase", session.session_id)
    repository.get = Mock(
        return_value=replace(
            record,
            session=record.session.model_copy(
                update={"state": "dispatch_pending", "temporal_run_id": None}
            ),
        )
    )
    for operation in (read_set, replace_set):
        with pytest.raises(DebugError) as failure:
            await operation(breakpoints)
        assert failure.value.code == "conflict"
    assert runtime.reads == [] and runtime.replacements == []
