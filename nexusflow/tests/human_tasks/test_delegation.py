"""Independent delegation acceptance against durable public service ports."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import DelegationRequest, TaskError
from nexusflow.tests.human_tasks.test_acceptance import (
    OTHER,
    SCOPE,
    Authorization,
    decision,
)
from nexusflow.tests.human_tasks.test_reassignment import change
from nexusflow.tests.human_tasks.test_sequential import Directory
from nexusflow.tests.human_tasks.test_sequential import request as sequential


def delegate(
    command: str = "delegate", revision: int = 1, target: str = "charlie"
) -> DelegationRequest:
    return DelegationRequest(command_id=command, expected_revision=revision, delegate_id=target)


def make_task(core: TaskService, rule: str = "any_one"):
    # This directory resolves explicit IDs faithfully; creation uses the group fixture.
    from nexusflow.modules.human_tasks.types import CreateTask

    return core.create(
        SCOPE,
        CreateTask(
            run_id="run",
            node_invocation_id="approval",
            definition_revision="release",
            rule=rule,
            user_ids=("alice", "bob"),
        ),
    )


@pytest.mark.parametrize("rule", ["any_one", "all_approvers"])
def test_delegate_replaces_only_actor_with_audited_snapshot(tmp_path: Path, rule: str) -> None:
    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = make_task(core, rule)
        updated = core.delegate(SCOPE, task.task_id, delegate())
        assert auth.calls[-1] == (SCOPE, "task.respond", task.task_id)
        assert updated.request == task.request
        assert set(updated.eligible_approver_ids) == {"charlie", "bob"}
        assert updated.revision == 2 and updated.status == "pending"
        record = updated.delegation_history[0]
        assert record.actor_id == "alice" and record.delegate_id == "charlie"
        assert record.previous_eligible_ids == task.eligible_approver_ids
        assert record.eligible_approver_ids == updated.eligible_approver_ids
        assert record.revision == 2 and record.created_at
        assert core.pending_completions(SCOPE) == ()
        assert core.list_tasks(SCOPE).items == ()
        for operation in (
            lambda: core.get(SCOPE, task.task_id),
            lambda: core.audit(SCOPE, task.task_id),
        ):
            with pytest.raises(TaskError) as denied:
                operation()
            assert denied.value.code == "forbidden"
        auth.actor = "charlie"
        assert core.list_tasks(SCOPE).items == (updated,)
        event = core.audit(SCOPE, task.task_id)[-1]
        assert event.kind == "delegated" and event.delegation == record
    finally:
        core.close()


def test_all_approvers_retains_other_votes_and_blocks_voter_delegation(tmp_path: Path) -> None:
    auth = Authorization("bob")
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = make_task(core, "all_approvers")
        voted = core.decide(SCOPE, task.task_id, decision("bob", 1))
        with pytest.raises(TaskError):
            core.delegate(SCOPE, task.task_id, delegate(revision=2))
        auth.actor = "alice"
        updated = core.delegate(SCOPE, task.task_id, delegate(revision=2))
        assert updated.decisions == voted.decisions
        auth.actor = "charlie"
        final = core.decide(SCOPE, task.task_id, decision("charlie", 3))
        assert final.status == "approved"
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()


def test_second_stage_repeated_actor_can_delegate_without_mutating_future_or_request(
    tmp_path: Path,
) -> None:
    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = core.create(SCOPE, sequential())
        advanced = core.decide(SCOPE, task.task_id, decision("manager", 1))
        updated = core.delegate(SCOPE, task.task_id, delegate(revision=2))
        assert updated.request == task.request
        assert updated.stage_assignments[0] == task.stage_assignments[0]
        assert set(updated.stage_assignments[1].eligible_approver_ids) == {"charlie", "bob"}
        assert updated.delegation_history[0].stage_id == "finance"
        assert updated.decisions == advanced.decisions
        auth.actor = "charlie"
        core.decide(SCOPE, task.task_id, decision("finance-charlie", 3))
        auth.actor = "bob"
        assert core.decide(SCOPE, task.task_id, decision("finance-bob", 4)).status == "approved"
    finally:
        core.close()


@pytest.mark.parametrize("target", ["alice", "bob"])
def test_self_or_existing_approver_rejected(tmp_path: Path, target: str) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory())
    try:
        task = make_task(core)
        with pytest.raises(TaskError):
            core.delegate(SCOPE, task.task_id, delegate(target=target))
        assert core.get(SCOPE, task.task_id) == task
    finally:
        core.close()


@pytest.mark.parametrize(
    "resolved", [(), ("unknown",), ("charlie", "extra"), ("charlie", "charlie"), ["charlie"]]
)
def test_directory_must_return_exact_known_target(tmp_path: Path, resolved) -> None:
    class BadDirectory(Directory):
        def resolve(self, scope, user_ids, group_ids):
            if user_ids == ("charlie",):
                return resolved
            return super().resolve(scope, user_ids, group_ids)

    core = TaskService(tmp_path / "tasks.db", Authorization(), BadDirectory())
    try:
        task = make_task(core)
        with pytest.raises(TaskError):
            core.delegate(SCOPE, task.task_id, delegate())
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
    finally:
        core.close()


def test_receipt_survives_restart_terminal_removed_access_and_directory_absence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.db"
    auth = Authorization()
    core = TaskService(path, auth, Directory())
    task = make_task(core)
    receipt = core.delegate(SCOPE, task.task_id, delegate())
    auth.actor = "charlie"
    terminal = core.decide(SCOPE, task.task_id, decision("done", 2))
    core.close()
    core = TaskService(path, auth)
    try:
        auth.actor = "alice"
        assert core.delegate(SCOPE, task.task_id, delegate()) == receipt
        auth.allowed = False
        with pytest.raises(TaskError) as failure:
            core.delegate(SCOPE, task.task_id, delegate())
        assert failure.value.code == "forbidden"
        auth.allowed, auth.actor = True, "outsider"
        with pytest.raises(TaskError) as failure:
            core.delegate(SCOPE, task.task_id, delegate())
        assert failure.value.code == "forbidden"
        auth.actor = "charlie"
        assert core.get(SCOPE, task.task_id) == terminal
    finally:
        core.close()


def test_scope_stale_and_cross_kind_command_conflicts(tmp_path: Path) -> None:
    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = make_task(core)
        with pytest.raises(TaskError) as failure:
            core.delegate(OTHER, task.task_id, delegate())
        assert failure.value.code == "not_found"
        core.delegate(SCOPE, task.task_id, delegate())
        with pytest.raises(TaskError) as failure:
            core.delegate(SCOPE, task.task_id, delegate(target="diana"))
        assert failure.value.code == "conflict"
        auth.actor = "bob"
        for operation in (
            lambda: core.delegate(SCOPE, task.task_id, delegate("stale", 1)),
            lambda: core.decide(SCOPE, task.task_id, decision("delegate", 2)),
            lambda: core.reassign(SCOPE, task.task_id, change("delegate", 2)),
        ):
            with pytest.raises(TaskError):
                operation()
    finally:
        core.close()


def test_combined_history_cap_does_not_block_committed_retry(tmp_path: Path) -> None:
    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = make_task(core)
        first = core.delegate(SCOPE, task.task_id, delegate())
        auth.actor = "charlie"
        current = core.delegate(SCOPE, task.task_id, delegate("back", 2, "alice"))
        auth.actor = "alice"
        from nexusflow.modules.human_tasks.types import ReassignmentRequest

        for index in range(98):
            current = core.reassign(
                SCOPE,
                task.task_id,
                ReassignmentRequest(
                    command_id=f"assign-{index}",
                    expected_revision=current.revision,
                    user_ids=("alice", "bob"),
                ),
            )
        assert len(current.assignment_history) + len(current.delegation_history) == 100
        with pytest.raises(TaskError):
            core.delegate(SCOPE, task.task_id, delegate("over", current.revision))
        assert core.delegate(SCOPE, task.task_id, delegate()) == first
    finally:
        core.close()


@pytest.mark.parametrize("decision_race", [False, True])
def test_racing_changes_commit_one_revision(tmp_path: Path, decision_race: bool) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    task = make_task(core)
    core.close()
    barrier = Barrier(2)

    def compete(index: int) -> str:
        service = TaskService(path, Authorization(), Directory())
        try:
            barrier.wait(timeout=10)
            try:
                if index and decision_race:
                    service.decide(SCOPE, task.task_id, decision("vote", 1))
                else:
                    service.delegate(
                        SCOPE, task.task_id, delegate(f"delegate-{index}", target=f"target-{index}")
                    )
                return "won"
            except TaskError:
                return "lost"
        finally:
            service.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, range(2))) == ["lost", "won"]
    core = TaskService(path, Authorization("bob"), Directory())
    try:
        final = core.get(SCOPE, task.task_id)
        assert final.revision == 2
        assert len(final.decisions) + len(final.delegation_history) == 1
        assert len(core.audit(SCOPE, task.task_id)) == 2
        assert len(core.pending_completions(SCOPE)) == len(final.decisions)
    finally:
        core.close()


def test_audit_failure_rolls_back_receipt_history_revision_and_outbox(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    try:
        task = make_task(core)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_delegation BEFORE INSERT ON human_events BEGIN SELECT RAISE(ABORT, 'audit failure'); END"
            )
        with pytest.raises(sqlite3.DatabaseError):
            core.delegate(SCOPE, task.task_id, delegate())
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
        assert core.pending_completions(SCOPE) == ()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (0,)
            db.execute("DROP TRIGGER fail_delegation")
        assert core.delegate(SCOPE, task.task_id, delegate()).revision == 2
    finally:
        core.close()


def test_own_receipt_after_stage_advance_and_future_snapshot_unchanged(tmp_path: Path) -> None:
    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = core.create(SCOPE, sequential())
        receipt = core.delegate(SCOPE, task.task_id, delegate())
        assert receipt.stage_assignments[1] == task.stage_assignments[1]
        assert receipt.delegation_history[0].stage_id == "manager"
        auth.actor = "charlie"
        advanced = core.decide(SCOPE, task.task_id, decision("manager", 2))
        assert advanced.stage_index == 1
        assert advanced.eligible_approver_ids == task.stage_assignments[1].eligible_approver_ids
        auth.actor = "alice"
        assert core.delegate(SCOPE, task.task_id, delegate()) == receipt
        assert core.get(SCOPE, task.task_id) == advanced
    finally:
        core.close()
