"""Independent notification acceptance through real task and SQLite boundaries."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from nexusflow.modules.human_tasks.notification_types import (
    NotificationOccurrence,
    NotificationSchedule,
)
from nexusflow.modules.human_tasks.notifications import TaskNotificationService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import ReassignmentRequest, TaskError
from nexusflow.tests.human_tasks.test_acceptance import (
    OTHER,
    SCOPE,
    Authorization,
    Directory,
    decision,
    request,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 24, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Sink:
    def __init__(self) -> None:
        self.calls: list[NotificationOccurrence] = []
        self.accepted: dict[str, NotificationOccurrence] = {}
        self.fail = False

    def deliver(self, occurrence: NotificationOccurrence) -> None:
        self.calls.append(occurrence)
        self.accepted.setdefault(occurrence.notification_id, occurrence)
        if self.fail:
            raise RuntimeError("secret-provider-token-and-sensitive-payload")


def schedule(clock: Clock, name: str = "reminder-1", revision: int = 1) -> NotificationSchedule:
    return NotificationSchedule(
        schedule_id=name,
        expected_revision=revision,
        due_at=clock.now + timedelta(seconds=10),
        kind="reminder",
    )


@pytest.fixture
def setup(tmp_path: Path):
    path = tmp_path / "tasks.db"
    auth, directory, clock = Authorization(), Directory(), Clock()
    core = TaskService(path, auth, directory)
    task = core.create(SCOPE, request("all_approvers"))
    notifications = TaskNotificationService(path, auth, clock=clock)
    try:
        yield path, auth, directory, clock, core, task, notifications
    finally:
        notifications.close()
        core.close()


def test_due_boundary_durable_snapshot_and_no_workflow_advancement(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    original = notifications.schedule(SCOPE, task.task_id, command)
    assert auth.calls[-1] == (SCOPE, "task.create", task.task_id)
    assert original.status == "pending" and original.attempts == 0
    occurrence = original.occurrence
    assert occurrence.scope == SCOPE
    assert occurrence.task_id == task.task_id
    assert occurrence.run_id == task.request.run_id
    assert occurrence.node_invocation_id == task.request.node_invocation_id
    assert occurrence.definition_revision == task.request.definition_revision
    assert occurrence.recipients == ("alice", "bob")
    assert occurrence.eligible_approver_ids == task.eligible_approver_ids
    assert core.get(SCOPE, task.task_id).revision == 2
    sink = Sink()
    clock.now = command.due_at - timedelta(microseconds=1)
    assert notifications.deliver_due(SCOPE, sink).delivered == 0
    assert sink.calls == []
    clock.now = command.due_at
    assert notifications.deliver_due(SCOPE, sink).delivered == 1
    assert sink.calls == [occurrence]
    receipt = notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert receipt.status == "delivered" and receipt.attempts == 1
    assert core.get(SCOPE, task.task_id).status == "pending"
    assert core.pending_completions(SCOPE) == ()
    assert notifications.deliver_due(SCOPE, sink).delivered == 0
    assert len(sink.calls) == 1


def test_exact_schedule_retry_survives_restart_terminal_and_changed_conflicts(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    original = notifications.schedule(SCOPE, task.task_id, command)
    core.decide(SCOPE, task.task_id, decision("reject", 2, "reject"))
    notifications.close()
    reopened = TaskNotificationService(path, auth, clock=clock)
    try:
        assert reopened.schedule(SCOPE, task.task_id, command) == original
        changed = command.model_copy(update={"kind": "escalation"})
        with pytest.raises(TaskError) as error:
            reopened.schedule(SCOPE, task.task_id, changed)
        assert error.value.code == "conflict"
        auth.allowed = False
        with pytest.raises(TaskError) as error:
            reopened.schedule(SCOPE, task.task_id, command)
        assert error.value.code == "forbidden"
    finally:
        reopened.close()


def test_lost_ack_retry_backoff_safe_errors_and_stable_deduplication(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    original = notifications.schedule(SCOPE, task.task_id, command)
    clock.now = command.due_at
    sink = Sink()
    sink.fail = True
    assert notifications.deliver_due(SCOPE, sink).failed == 1
    failed = notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert failed.status == "pending" and failed.attempts == 1
    assert failed.last_error == "delivery_unavailable"
    assert "secret" not in failed.model_dump_json()
    assert failed.next_attempt_at == clock.now + timedelta(seconds=60)
    assert notifications.deliver_due(SCOPE, sink).failed == 0
    clock.now = failed.next_attempt_at - timedelta(microseconds=1)
    assert notifications.deliver_due(SCOPE, sink).delivered == 0
    notifications.close()
    reopened = TaskNotificationService(path, auth, clock=clock)
    try:
        clock.now = failed.next_attempt_at
        sink.fail = False
        assert reopened.deliver_due(SCOPE, sink).delivered == 1
        assert sink.calls == [original.occurrence, original.occurrence]
        assert len(sink.accepted) == 1
        delivered = reopened.get(SCOPE, task.task_id, command.schedule_id)
        assert delivered.attempts == 2 and delivered.last_error is None
    finally:
        reopened.close()


@pytest.mark.parametrize("change", ["terminal", "assignment", "vote"])
def test_changed_task_cancels_stale_recipients_without_send(setup, change: str) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    notifications.schedule(SCOPE, task.task_id, command)
    if change == "assignment":
        directory.resolved = ("charlie",)
        core.reassign(
            SCOPE,
            task.task_id,
            ReassignmentRequest(command_id="replace", expected_revision=2, user_ids=("charlie",)),
        )
        auth.actor = "charlie"
    else:
        core.decide(
            SCOPE,
            task.task_id,
            decision("vote", 2, "reject" if change == "terminal" else "approve"),
        )
    clock.now = command.due_at
    sink = Sink()
    batch = notifications.deliver_due(SCOPE, sink)
    assert batch.cancelled == 1 and batch.delivered == batch.failed == 0
    assert sink.calls == []
    assert notifications.get(SCOPE, task.task_id, command.schedule_id).status == "cancelled"


def test_new_schedule_excludes_voted_approvers_and_checks_revision(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    core.decide(SCOPE, task.task_id, decision("vote", 1))
    with pytest.raises(TaskError) as error:
        notifications.schedule(SCOPE, task.task_id, schedule(clock))
    assert error.value.code == "conflict"
    fresh = notifications.schedule(SCOPE, task.task_id, schedule(clock, revision=2))
    assert fresh.occurrence.recipients == ("bob",)
    assert fresh.occurrence.eligible_approver_ids == ("alice", "bob")
    assert core.get(SCOPE, task.task_id).revision == 3


def test_scope_and_fresh_read_authorization(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    notifications.schedule(SCOPE, task.task_id, command)
    for operation in (
        lambda: notifications.get(OTHER, task.task_id, command.schedule_id),
        lambda: notifications.schedule(OTHER, task.task_id, command),
    ):
        with pytest.raises(TaskError) as error:
            operation()
        assert error.value.code == "not_found"
    sink = Sink()
    clock.now = command.due_at
    assert notifications.deliver_due(OTHER, sink).delivered == 0
    auth.actor = "outsider"
    with pytest.raises(TaskError) as error:
        notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert error.value.code == "forbidden"
    auth.actor, auth.allowed = "alice", False
    with pytest.raises(TaskError) as error:
        notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert error.value.code == "forbidden"


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_dispatch_count_bounds(setup, limit) -> None:
    notifications = setup[-1]
    with pytest.raises((TaskError, ValueError)):
        notifications.deliver_due(SCOPE, Sink(), limit=limit)


def test_audit_failure_rolls_back_ack_and_retries_identical_occurrence(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    occurrence = notifications.schedule(SCOPE, task.task_id, command).occurrence
    clock.now = command.due_at
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_notification_audit BEFORE INSERT ON human_events BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
        )
    sink = Sink()
    with pytest.raises(sqlite3.DatabaseError):
        notifications.deliver_due(SCOPE, sink)
    assert sink.calls == [occurrence]
    pending = notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert pending.status == "pending" and pending.attempts == 0
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_notification_audit")
    assert notifications.deliver_due(SCOPE, sink).delivered == 1
    assert sink.calls == [occurrence, occurrence]
    assert len(sink.accepted) == 1


def test_callback_holds_writer_lock_then_decision_can_commit(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    notifications.schedule(SCOPE, task.task_id, command)
    clock.now = command.due_at

    class LockProbe(Sink):
        def deliver(self, occurrence: NotificationOccurrence) -> None:
            with sqlite3.connect(path, timeout=0) as connection:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    connection.execute("BEGIN IMMEDIATE")
            super().deliver(occurrence)

    assert notifications.deliver_due(SCOPE, LockProbe()).delivered == 1
    assert (
        core.decide(SCOPE, task.task_id, decision("after-send", 2, "reject")).status == "rejected"
    )


def test_two_dispatchers_commit_one_delivery_ack(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    notifications.schedule(SCOPE, task.task_id, command)
    clock.now = command.due_at
    barrier = Barrier(2)
    sink = Sink()

    def dispatch(_: int) -> int:
        worker = TaskNotificationService(path, Authorization(), clock=clock)
        try:
            barrier.wait(timeout=10)
            return worker.deliver_due(SCOPE, sink).delivered
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(dispatch, range(2))) == [0, 1]
    assert len(sink.calls) == 1
    assert notifications.get(SCOPE, task.task_id, command.schedule_id).attempts == 1


def test_provider_failure_continues_batch_and_never_reverses_later_approval(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    first = notifications.schedule(SCOPE, task.task_id, schedule(clock, "first", 1))
    second = notifications.schedule(SCOPE, task.task_id, schedule(clock, "second", 2))
    clock.now = first.request.due_at

    class PartialSink(Sink):
        def deliver(self, occurrence: NotificationOccurrence) -> None:
            self.fail = occurrence.notification_id == first.occurrence.notification_id
            super().deliver(occurrence)

    sink = PartialSink()
    result = notifications.deliver_due(SCOPE, sink)
    assert result.failed == result.delivered == 1
    assert result.cancelled == 0
    assert {item.notification_id for item in sink.calls} == {
        first.occurrence.notification_id,
        second.occurrence.notification_id,
    }
    approved = core.decide(SCOPE, task.task_id, decision("alice", 3))
    auth.actor = "bob"
    approved = core.decide(SCOPE, task.task_id, decision("bob", approved.revision))
    assert approved.status == "approved"
    clock.now += timedelta(seconds=60)
    assert notifications.deliver_due(SCOPE, sink).cancelled == 1
    assert len(sink.calls) == 2
    assert core.get(SCOPE, task.task_id) == approved
    assert len(core.pending_completions(SCOPE)) == 1
    # A cancelled retry may already have reached the remote sink before its lost ACK.
    assert first.occurrence.notification_id in sink.accepted


def test_schedule_audit_failure_rolls_back_revision_and_allows_clean_retry(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    command = schedule(clock)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_schedule_audit BEFORE INSERT ON human_events "
            "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        notifications.schedule(SCOPE, task.task_id, command)
    assert core.get(SCOPE, task.task_id) == task
    with pytest.raises(TaskError) as error:
        notifications.get(SCOPE, task.task_id, command.schedule_id)
    assert error.value.code == "not_found"
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER fail_schedule_audit")
    assert notifications.schedule(SCOPE, task.task_id, command).status == "pending"
    assert core.get(SCOPE, task.task_id).revision == 2


def test_schedule_limit_keeps_exact_receipt_retry_available(setup) -> None:
    path, auth, directory, clock, core, task, notifications = setup
    original_request = schedule(clock)
    original = notifications.schedule(SCOPE, task.task_id, original_request)
    for index in range(1, 1000):
        notifications.schedule(SCOPE, task.task_id, schedule(clock, f"schedule-{index}", index + 1))
    with pytest.raises(TaskError) as error:
        notifications.schedule(SCOPE, task.task_id, schedule(clock, "over-limit", 1001))
    assert error.value.code == "conflict"
    assert notifications.schedule(SCOPE, task.task_id, original_request) == original
    assert core.get(SCOPE, task.task_id).revision == 1001
    assert len(core.audit(SCOPE, task.task_id)) == 1001
