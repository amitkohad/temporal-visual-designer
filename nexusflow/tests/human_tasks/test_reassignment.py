"""Independent reassignment acceptance against persisted public service ports."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

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


def change(command: str = "change", revision: int = 1) -> ReassignmentRequest:
    return ReassignmentRequest(
        command_id=command, expected_revision=revision, group_ids=("purchasing",)
    )


def test_explicit_refresh_replaces_membership_with_audited_snapshot(tmp_path: Path) -> None:
    auth, directory = Authorization(), Directory()
    core = TaskService(tmp_path / "tasks.db", auth, directory)
    try:
        original = core.create(SCOPE, request())
        directory.resolved = ("charlie", "charlie", "diana")
        assert core.get(SCOPE, original.task_id) == original
        auth.actor = "charlie"
        assert core.list_tasks(SCOPE).items == ()
        # A freshly authorized administrator need not be an eligible approver.
        auth.actor = "administrator"
        updated = core.reassign(SCOPE, original.task_id, change())
        assert auth.calls[-1] == (SCOPE, "task.reassign", original.task_id)
        assert updated.request == original.request
        assert updated.revision == 2
        assert updated.status == "pending"
        assert updated.decisions == ()
        assert updated.eligible_approver_ids == ("charlie", "diana")
        record = updated.assignment_history[0]
        assert record.actor_id == "administrator"
        assert record.command_id == "change"
        assert record.previous_eligible_ids == original.eligible_approver_ids
        assert record.eligible_approver_ids == updated.eligible_approver_ids
        assert record.group_ids == ("purchasing",)
        assert record.revision == 2
        auth.actor = "alice"
        assert core.list_tasks(SCOPE).items == ()
        for operation in (
            lambda: core.get(SCOPE, original.task_id),
            lambda: core.audit(SCOPE, original.task_id),
            lambda: core.decide(SCOPE, original.task_id, decision("former", 2)),
        ):
            with pytest.raises(TaskError) as denied:
                operation()
            assert denied.value.code == "forbidden"
        auth.actor = "charlie"
        assert core.list_tasks(SCOPE).items == (updated,)
        event = core.audit(SCOPE, original.task_id)[-1]
        assert event.kind == "reassigned"
        assert event.assignment == record
        assert event.actor_id == "administrator"
        assert core.pending_completions(SCOPE) == ()
        assert core.decide(SCOPE, original.task_id, decision("new", 2)).status == "approved"
        # An existing invocation remains idempotent against its original definition.
        assert core.create(SCOPE, request()).request == original.request
    finally:
        core.close()


def test_receipt_survives_restart_later_changes_and_directory_outage(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    auth, directory = Authorization("administrator"), Directory()
    core = TaskService(path, auth, directory)
    task = core.create(SCOPE, request())
    command = change()
    first = core.reassign(SCOPE, task.task_id, command)
    second = core.reassign(SCOPE, task.task_id, change("next", 2))
    core.close()
    core = TaskService(path, auth)
    try:
        assert core.reassign(SCOPE, task.task_id, command) == first
        auth.actor = "alice"
        assert core.get(SCOPE, task.task_id) == second
        assert len(core.audit(SCOPE, task.task_id)) == 3
        auth.actor = "administrator"
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, change("missing-directory", 3))
        assert failure.value.code == "assignment_unavailable"
        auth.actor = "another-administrator"
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, command)
        assert failure.value.code == "conflict"
        auth.actor = "administrator"
        auth.allowed = False
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, command)
        assert failure.value.code == "forbidden"
    finally:
        core.close()


@pytest.mark.parametrize("resolved", [(), ("invalid/id",), ("alice",) * 1001, ["alice"]])
def test_resolution_failure_never_changes_task_or_receipts(
    tmp_path: Path, resolved: tuple[str, ...]
) -> None:
    path = tmp_path / "tasks.db"
    directory = Directory()
    core = TaskService(path, Authorization(), directory)
    try:
        task = core.create(SCOPE, request())
        directory.resolved = resolved
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, change())
        assert failure.value.code == "assignment_unavailable"
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (0,)
    finally:
        core.close()


def test_scope_stale_request_and_cross_action_command_collisions(tmp_path: Path) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory())
    try:
        task = core.create(SCOPE, request("all_approvers"))
        with pytest.raises(TaskError) as failure:
            core.reassign(OTHER, task.task_id, change())
        assert failure.value.code == "not_found"
        updated = core.reassign(SCOPE, task.task_id, change())
        for command in (change("stale", 1), change("change", 2)):
            with pytest.raises(TaskError) as failure:
                core.reassign(SCOPE, task.task_id, command)
            assert failure.value.code == "conflict"
        with pytest.raises(TaskError) as failure:
            core.decide(SCOPE, task.task_id, decision("change", 2))
        assert failure.value.code == "conflict"
        voted = core.decide(SCOPE, task.task_id, decision("vote", 2))
        assert voted.status == "pending"
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, change("vote", 3))
        assert failure.value.code == "conflict"
        assert core.get(SCOPE, task.task_id) == voted
        assert len(updated.assignment_history) == 1
    finally:
        core.close()


@pytest.mark.parametrize("rule", ["any_one", "all_approvers"])
def test_no_reassignment_after_first_vote(tmp_path: Path, rule: str) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory())
    try:
        task = core.create(SCOPE, request(rule))
        voted = core.decide(SCOPE, task.task_id, decision("vote", 1))
        with pytest.raises(TaskError) as failure:
            core.reassign(SCOPE, task.task_id, change("after-vote", 2))
        assert failure.value.code == "conflict"
        assert core.get(SCOPE, task.task_id) == voted
    finally:
        core.close()


def test_bounded_history_keeps_exact_retry_available(tmp_path: Path) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory())
    try:
        task = core.create(SCOPE, request())
        first = core.reassign(SCOPE, task.task_id, change())
        current = first
        for index in range(1, 100):
            current = core.reassign(
                SCOPE, task.task_id, change(f"change-{index}", current.revision)
            )
        assert len(current.assignment_history) == 100
        with pytest.raises(TaskError):
            core.reassign(SCOPE, task.task_id, change("over-limit", current.revision))
        assert core.reassign(SCOPE, task.task_id, change()) == first
        assert core.get(SCOPE, task.task_id) == current
        assert len(core.audit(SCOPE, task.task_id)) == 101
    finally:
        core.close()


def test_decision_and_reassignment_race_has_one_atomic_winner(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    setup = TaskService(path, Authorization(), Directory())
    task = setup.create(SCOPE, request())
    setup.close()
    barrier = Barrier(2)

    def compete(reassign: bool) -> str:
        core = TaskService(path, Authorization(), Directory(("bob",)))
        try:
            barrier.wait(timeout=10)
            try:
                if reassign:
                    core.reassign(SCOPE, task.task_id, change())
                else:
                    core.decide(SCOPE, task.task_id, decision("vote", 1))
                return "won"
            except TaskError:
                return "lost"
        finally:
            core.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, (True, False)))
    assert sorted(outcomes) == ["lost", "won"]
    core = TaskService(path, Authorization("bob"), Directory())
    try:
        final = core.get(SCOPE, task.task_id)
        assert final.revision == 2
        assert len(final.decisions) + len(final.assignment_history) == 1
        assert len(core.audit(SCOPE, task.task_id)) == 2
        assert len(core.pending_completions(SCOPE)) == len(final.decisions)
    finally:
        core.close()


def test_audit_failure_rolls_back_assignment_and_command_receipt(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    try:
        task = core.create(SCOPE, request())
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_reassignment_audit BEFORE INSERT ON human_events "
                "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"
            )
        with pytest.raises(sqlite3.DatabaseError):
            core.reassign(SCOPE, task.task_id, change())
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (0,)
            connection.execute("DROP TRIGGER fail_reassignment_audit")
        updated = core.reassign(SCOPE, task.task_id, change())
        assert updated.revision == 2
        assert len(core.audit(SCOPE, task.task_id)) == 2
    finally:
        core.close()


def test_explicit_user_reassignment_and_terminal_retry_keep_committed_receipt(
    tmp_path: Path,
) -> None:
    class RecordingDirectory(Directory):
        supplied: tuple[tuple[str, ...], tuple[str, ...]] | None = None

        def resolve(self, scope, user_ids, group_ids):
            assert scope == SCOPE
            self.supplied = (user_ids, group_ids)
            return super().resolve(scope, user_ids, group_ids)

    path = tmp_path / "tasks.db"
    auth, directory = Authorization("administrator"), RecordingDirectory()
    core = TaskService(path, auth, directory)
    try:
        task = core.create(SCOPE, request())
        directory.resolved = ("replacement",)
        command = ReassignmentRequest(
            command_id="explicit-user", expected_revision=1, user_ids=("replacement",)
        )
        receipt = core.reassign(SCOPE, task.task_id, command)
        assert directory.supplied == (("replacement",), ())
        assert receipt.assignment_history[0].user_ids == ("replacement",)
        assert receipt.assignment_history[0].group_ids == ()
        auth.actor = "replacement"
        terminal = core.decide(SCOPE, task.task_id, decision("completed", 2))
        assert terminal.status == "approved"
        core.close()
        # No resolver is required to recover the earlier committed response.
        core = TaskService(path, auth)
        auth.actor = "administrator"
        assert core.reassign(SCOPE, task.task_id, command) == receipt
        auth.actor = "replacement"
        assert core.get(SCOPE, task.task_id) == terminal
        assert len(core.audit(SCOPE, task.task_id)) == 3
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()
