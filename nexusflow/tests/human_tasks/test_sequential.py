"""Sequential approval acceptance using public ports and durable SQLite state."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import ApprovalStage, CreateTask, TaskError
from nexusflow.tests.human_tasks.test_acceptance import SCOPE, Authorization, decision


class Directory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on: str | None = None
        self.extra: tuple[str, ...] = ()

    def resolve(self, scope, user_ids, group_ids):
        self.calls.append(user_ids)
        if self.fail_on in user_ids:
            raise TaskError("unavailable", "Directory unavailable")
        return user_ids + self.extra


def request(*stages: ApprovalStage) -> CreateTask:
    return CreateTask(
        run_id="run",
        node_invocation_id="approval",
        definition_revision="release",
        rule="sequential",
        stages=stages
        or (
            ApprovalStage(stage_id="manager", rule="any_one", user_ids=("alice",)),
            ApprovalStage(stage_id="finance", rule="all_approvers", user_ids=("alice", "bob")),
        ),
    )


def test_any_then_all_snapshots_repeated_actor_and_final_completion(tmp_path: Path) -> None:
    auth, directory = Authorization(), Directory()
    core = TaskService(tmp_path / "tasks.db", auth, directory)
    try:
        task = core.create(SCOPE, request())
        assert len(directory.calls) == 2
        assert task.stage_index == 0
        assert task.eligible_approver_ids == ("alice",)
        assert len(task.stage_assignments) == 2
        directory.fail_on = "alice"
        advanced = core.decide(SCOPE, task.task_id, decision("manager", 1))
        assert advanced.status == "pending"
        assert advanced.stage_index == 1
        assert advanced.revision == 2
        assert set(advanced.eligible_approver_ids) == {"alice", "bob"}
        assert core.pending_completions(SCOPE) == ()
        partial = core.decide(SCOPE, task.task_id, decision("finance-alice", 2))
        assert partial.status == "pending"
        assert partial.stage_index == 1
        auth.actor = "bob"
        final = core.decide(SCOPE, task.task_id, decision("finance-bob", 3))
        assert final.status == "approved"
        assert [vote.stage_id for vote in final.decisions] == ["manager", "finance", "finance"]
        assert len(core.pending_completions(SCOPE)) == 1
        assert [event.kind for event in core.audit(SCOPE, task.task_id)].count(
            "stage_advanced"
        ) == 1
    finally:
        core.close()


def test_current_assignment_reads_votes_inbox_and_old_receipt_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    auth = Authorization()
    core = TaskService(path, auth, Directory())
    task = core.create(
        SCOPE,
        request(
            ApprovalStage(stage_id="first", rule="any_one", user_ids=("alice",)),
            ApprovalStage(stage_id="second", rule="any_one", user_ids=("bob",)),
        ),
    )
    auth.actor = "bob"
    assert core.list_tasks(SCOPE).items == ()
    for call in (
        lambda: core.get(SCOPE, task.task_id),
        lambda: core.audit(SCOPE, task.task_id),
        lambda: core.decide(SCOPE, task.task_id, decision("future", 1)),
    ):
        with pytest.raises(TaskError):
            call()
    auth.actor = "alice"
    command = decision("first-vote", 1)
    receipt = core.decide(SCOPE, task.task_id, command)
    assert core.list_tasks(SCOPE).items == ()
    core.close()
    core = TaskService(path, auth, Directory())
    try:
        assert core.decide(SCOPE, task.task_id, command) == receipt
        for call in (
            lambda: core.get(SCOPE, task.task_id),
            lambda: core.audit(SCOPE, task.task_id),
            lambda: core.decide(SCOPE, task.task_id, decision("past", 2)),
        ):
            with pytest.raises(TaskError):
                call()
        auth.allowed = False
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, command)
        auth.allowed, auth.actor = True, "bob"
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, command)
        assert len(core.list_tasks(SCOPE).items) == 1
        assert core.decide(SCOPE, task.task_id, decision("final", 2)).status == "approved"
    finally:
        core.close()


@pytest.mark.parametrize("later", [False, True])
def test_rejection_in_any_stage_is_terminal(tmp_path: Path, later: bool) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory())
    try:
        task = core.create(SCOPE, request())
        revision = 1
        if later:
            core.decide(SCOPE, task.task_id, decision("advance", revision))
            revision += 1
        rejected = core.decide(SCOPE, task.task_id, decision("reject", revision, "reject"))
        assert rejected.status == "rejected"
        assert len(core.pending_completions(SCOPE)) == 1
        assert core.pending_completions(SCOPE)[0].outcome == "rejected"
    finally:
        core.close()


def test_later_directory_failure_and_empty_resolution_create_nothing(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    directory = Directory()
    directory.fail_on = "bob"
    core = TaskService(path, Authorization(), directory)
    try:
        with pytest.raises(TaskError):
            core.create(SCOPE, request())
        with sqlite3.connect(path) as db:
            for table in ("human_tasks", "human_events", "human_commands", "human_outbox"):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
        directory.fail_on = None
        assert core.create(SCOPE, request()).revision == 1
    finally:
        core.close()


@pytest.mark.parametrize(
    "change",
    [
        {"stages": ()},
        {"user_ids": ("alice",)},
        {"group_ids": ("group",)},
        {"stages": ({"stage_id": "empty", "rule": "any_one"},)},
        {"stages": ({"stage_id": "nested", "rule": "sequential", "user_ids": ("alice",)},)},
        {
            "stages": tuple(
                {"stage_id": "duplicate", "rule": "any_one", "user_ids": ("alice",)}
                for _ in range(2)
            )
        },
        {
            "stages": tuple(
                {"stage_id": f"s{i}", "rule": "any_one", "user_ids": ("alice",)} for i in range(21)
            )
        },
        {"rule": "any_one", "user_ids": ("alice",)},
    ],
)
def test_invalid_sequential_configs_are_rejected(change: dict) -> None:
    values = request().model_dump()
    values.update(change)
    with pytest.raises(ValidationError):
        CreateTask.model_validate(values)


def test_stage_transition_event_failure_rolls_back_decision_and_receipt(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    try:
        task = core.create(SCOPE, request())
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_advance BEFORE INSERT ON human_events "
                "WHEN json_extract(NEW.event, '$.kind') = 'stage_advanced' "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        command = decision("advance", 1)
        with pytest.raises(sqlite3.DatabaseError):
            core.decide(SCOPE, task.task_id, command)
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
        assert core.pending_completions(SCOPE) == ()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (0,)
            db.execute("DROP TRIGGER fail_advance")
        assert core.decide(SCOPE, task.task_id, command).stage_index == 1
    finally:
        core.close()


def test_two_connections_race_stage_boundary_without_double_advancement(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    task = core.create(
        SCOPE,
        request(
            ApprovalStage(stage_id="first", rule="any_one", user_ids=("alice", "bob")),
            ApprovalStage(stage_id="second", rule="all_approvers", user_ids=("alice", "bob")),
        ),
    )
    core.close()
    barrier = Barrier(2)

    def vote(actor: str) -> bool:
        connection = TaskService(path, Authorization(actor), Directory())
        try:
            barrier.wait(timeout=10)
            try:
                connection.decide(SCOPE, task.task_id, decision(actor, 1))
                return True
            except TaskError:
                return False
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(vote, ("alice", "bob"))) == 1
    core = TaskService(path, Authorization(), Directory())
    try:
        current = core.get(SCOPE, task.task_id)
        assert current.stage_index == 1
        assert current.revision == 2
        assert current.status == "pending"
        assert len(current.decisions) == 1
        assert core.pending_completions(SCOPE) == ()
        assert [event.kind for event in core.audit(SCOPE, task.task_id)].count(
            "stage_advanced"
        ) == 1
    finally:
        core.close()


def test_reassignment_updates_only_first_snapshot_before_any_vote(tmp_path: Path) -> None:
    from nexusflow.modules.human_tasks.types import ReassignmentRequest

    auth = Authorization()
    core = TaskService(tmp_path / "tasks.db", auth, Directory())
    try:
        task = core.create(SCOPE, request())
        replaced = core.reassign(
            SCOPE,
            task.task_id,
            ReassignmentRequest(
                command_id="replace",
                expected_revision=1,
                user_ids=("charlie",),
            ),
        )
        assert replaced.request == task.request
        assert replaced.eligible_approver_ids == ("charlie",)
        assert replaced.stage_assignments[0].eligible_approver_ids == ("charlie",)
        assert replaced.stage_assignments[1] == task.stage_assignments[1]
        auth.actor = "charlie"
        advanced = core.decide(SCOPE, task.task_id, decision("charlie", 2))
        assert advanced.stage_index == 1
        with pytest.raises(TaskError):
            core.reassign(
                SCOPE,
                task.task_id,
                ReassignmentRequest(
                    command_id="late",
                    expected_revision=3,
                    user_ids=("charlie",),
                ),
            )
    finally:
        core.close()


@pytest.mark.parametrize("count,valid", [(500, True), (501, False)])
def test_total_snapshot_bound_counts_stage_memberships(
    tmp_path: Path, count: int, valid: bool
) -> None:
    directory = Directory()
    directory.extra = tuple(f"user-{i}" for i in range(count - 1))
    core = TaskService(tmp_path / "tasks.db", Authorization(), directory)
    configured = request(
        ApprovalStage(stage_id="first", rule="any_one", user_ids=("alice",)),
        ApprovalStage(stage_id="second", rule="any_one", user_ids=("alice",)),
    )
    try:
        if valid:
            task = core.create(SCOPE, configured)
            assert sum(len(stage.eligible_approver_ids) for stage in task.stage_assignments) == 1000
        else:
            with pytest.raises(TaskError):
                core.create(SCOPE, configured)
            assert core.list_tasks(SCOPE).items == ()
    finally:
        core.close()


def test_shared_form_validated_once_and_readonly_after_stage_advances(tmp_path: Path) -> None:
    from nexusflow.modules.designer_forms.form_types import FormError
    from nexusflow.modules.human_tasks.forms import TaskFormService
    from nexusflow.tests.human_tasks.test_task_forms import Policy, definition

    path = tmp_path / "tasks.db"
    auth = Authorization()
    core = TaskService(path, auth, Directory())
    forms = TaskFormService(path, auth, Policy())
    try:
        task = core.create(SCOPE, request())
        assert forms.bind(SCOPE, task.task_id, definition(), "{}") == 2
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, decision("invalid", 2))
        assert forms.submit(SCOPE, task.task_id, 2, '{"name":"Ada","amount":10}') == 3
        advanced = core.decide(SCOPE, task.task_id, decision("first", 3))
        assert advanced.stage_index == 1
        with pytest.raises((TaskError, FormError)):
            forms.submit(SCOPE, task.task_id, 4, '{"amount":20}')
        partial = core.decide(SCOPE, task.task_id, decision("finance-alice", 4))
        auth.actor = "bob"
        assert (
            core.decide(SCOPE, task.task_id, decision("finance-bob", partial.revision)).status
            == "approved"
        )
    finally:
        forms.close()
        core.close()


def test_empty_later_stage_resolution_is_atomic(tmp_path: Path) -> None:
    class EmptyLater(Directory):
        def resolve(self, scope, user_ids, group_ids):
            return () if "bob" in user_ids else user_ids

    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), EmptyLater())
    try:
        with pytest.raises(TaskError):
            core.create(SCOPE, request())
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM human_tasks").fetchone() == (0,)
            assert db.execute("SELECT COUNT(*) FROM human_events").fetchone() == (0,)
    finally:
        core.close()


def test_final_outbox_failure_rolls_back_only_final_vote(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    try:
        task = core.create(
            SCOPE,
            request(
                ApprovalStage(stage_id="first", rule="any_one", user_ids=("alice",)),
                ApprovalStage(stage_id="second", rule="any_one", user_ids=("alice",)),
            ),
        )
        advanced = core.decide(SCOPE, task.task_id, decision("advance", 1))
        audit = core.audit(SCOPE, task.task_id)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TRIGGER fail_final BEFORE INSERT ON human_outbox "
                "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
            )
        command = decision("final", 2)
        with pytest.raises(sqlite3.DatabaseError):
            core.decide(SCOPE, task.task_id, command)
        assert core.get(SCOPE, task.task_id) == advanced
        assert core.audit(SCOPE, task.task_id) == audit
        assert core.pending_completions(SCOPE) == ()
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (1,)
            db.execute("DROP TRIGGER fail_final")
        assert core.decide(SCOPE, task.task_id, command).status == "approved"
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()
