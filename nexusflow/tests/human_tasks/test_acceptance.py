"""Bounded core acceptance through public ports and real SQLite connections."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import (
    Completion,
    CreateTask,
    DecisionRequest,
    TaskError,
)

SCOPE = Scope(organization_id="org", environment_id="dev")
OTHER = Scope(organization_id="other", environment_id="dev")


class Authorization:
    def __init__(self, actor: str = "alice") -> None:
        self.actor = actor
        self.allowed = True
        self.calls: list[tuple[Scope, str, str]] = []

    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str:
        self.calls.append((scope, permission, resource_id))
        if not self.allowed:
            raise TaskError("forbidden", "Access revoked")
        return self.actor


class Directory:
    def __init__(self, resolved: tuple[str, ...] = ("alice", "bob", "alice")) -> None:
        self.resolved = resolved
        self.available = True
        self.calls = 0

    def resolve(
        self, scope: Scope, user_ids: tuple[str, ...], group_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        self.calls += 1
        if not self.available:
            raise TaskError("unavailable", "Directory unavailable")
        return self.resolved


class Sink:
    def __init__(self) -> None:
        self.available = True
        self.delivered: dict[str, Completion] = {}

    def deliver(self, completion: Completion) -> None:
        if not self.available:
            raise RuntimeError("Destination unavailable")
        self.delivered.setdefault(completion.completion_id, completion)


def request(rule: str = "any_one") -> CreateTask:
    return CreateTask.model_validate(
        {
            "run_id": "run-1",
            "node_invocation_id": "node-1",
            "definition_revision": "release-1",
            "rule": rule,
            "group_ids": ("purchasing",),
        }
    )


def decision(command: str, revision: int, vote: str = "approve") -> DecisionRequest:
    return DecisionRequest.model_validate(
        {"command_id": command, "expected_revision": revision, "decision": vote}
    )


def service(path: Path, auth: Authorization | None = None) -> TaskService:
    return TaskService(path, auth or Authorization(), Directory())


def test_creation_snapshot_idempotency_and_collision(tmp_path: Path) -> None:
    auth, directory = Authorization(), Directory()
    core = TaskService(tmp_path / "tasks.db", auth, directory)
    try:
        first = core.create(SCOPE, request())
        assert set(first.eligible_approver_ids) == {"alice", "bob"}
        assert len(first.eligible_approver_ids) == 2
        directory.resolved = ("charlie",)
        assert core.create(SCOPE, request()) == first
        with pytest.raises(TaskError):
            core.create(SCOPE, request("all_approvers"))
        assert core.get(SCOPE, first.task_id) == first
        assert core.audit(SCOPE, first.task_id)[0].kind == "created"
        assert auth.calls
    finally:
        core.close()


@pytest.mark.parametrize(
    "resolved",
    [(), ("",), ("invalid/user",), ("x",) * 1001, (123,), ["alice"]],
)
def test_bad_directory_resolution_fails_closed(tmp_path: Path, resolved: tuple[str, ...]) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(), Directory(resolved))
    try:
        with pytest.raises((TaskError, ValidationError)):
            core.create(SCOPE, request())
        assert core.pending_completions(SCOPE) == ()
    finally:
        core.close()


def test_directory_outage_and_empty_declarations(tmp_path: Path) -> None:
    directory = Directory()
    directory.available = False
    core = TaskService(tmp_path / "tasks.db", Authorization(), directory)
    try:
        with pytest.raises(TaskError):
            core.create(SCOPE, request())
        with pytest.raises(ValidationError):
            CreateTask(
                run_id="run",
                node_invocation_id="node",
                definition_revision="release",
                rule="any_one",
            )
    finally:
        core.close()


@pytest.mark.parametrize("rule", ["any_one", "all_approvers"])
def test_approval_and_rejection_rules(tmp_path: Path, rule: str) -> None:
    auth = Authorization()
    core = service(tmp_path / "tasks.db", auth)
    try:
        task = core.create(SCOPE, request(rule))
        first = core.decide(SCOPE, task.task_id, decision("alice-yes", 1))
        if rule == "all_approvers":
            assert first.status == "pending"
            assert core.pending_completions(SCOPE) == ()
            auth.actor = "bob"
            first = core.decide(SCOPE, task.task_id, decision("bob-yes", 2))
        assert first.status == "approved"
        assert len(core.pending_completions(SCOPE)) == 1
        rejected_task = core.create(
            SCOPE, request(rule).model_copy(update={"node_invocation_id": "reject-node"})
        )
        rejected = core.decide(SCOPE, rejected_task.task_id, decision("no", 1, "reject"))
        assert rejected.status == "rejected"
    finally:
        core.close()


def test_fresh_authorization_scope_and_non_assignee_denials(tmp_path: Path) -> None:
    auth = Authorization()
    core = service(tmp_path / "tasks.db", auth)
    try:
        task = core.create(SCOPE, request())
        for operation in (
            lambda: core.get(OTHER, task.task_id),
            lambda: core.audit(OTHER, task.task_id),
            lambda: core.decide(OTHER, task.task_id, decision("foreign", 1)),
        ):
            with pytest.raises(TaskError):
                operation()
        auth.actor = "outsider"
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, decision("outside", 1))
        auth.allowed = False
        for operation in (
            lambda: core.get(SCOPE, task.task_id),
            lambda: core.audit(SCOPE, task.task_id),
            lambda: core.decide(SCOPE, task.task_id, decision("revoked", 1)),
            lambda: core.create(SCOPE, request()),
        ):
            with pytest.raises(TaskError):
                operation()
        assert core.pending_completions(SCOPE) == ()
    finally:
        core.close()


def test_command_snapshot_retry_collision_stale_and_revocation(tmp_path: Path) -> None:
    auth = Authorization()
    core = service(tmp_path / "tasks.db", auth)
    try:
        task = core.create(SCOPE, request("all_approvers"))
        command = decision("alice", 1)
        first = core.decide(SCOPE, task.task_id, command)
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, decision("alice", 1, "reject"))
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, decision("stale", 1))
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, decision("second-alice", 2))
        auth.actor = "bob"
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, command)
        terminal = core.decide(SCOPE, task.task_id, decision("bob", 2))
        assert terminal.status == "approved"
        auth.actor = "alice"
        assert core.decide(SCOPE, task.task_id, command) == first
        assert core.get(SCOPE, task.task_id) == terminal
        assert len(core.audit(SCOPE, task.task_id)) == 3
        auth.allowed = False
        with pytest.raises(TaskError):
            core.decide(SCOPE, task.task_id, command)
    finally:
        core.close()


def test_sqlite_reopen_and_delivery_failure_recovery(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = service(path)
    task = core.create(SCOPE, request())
    approved = core.decide(SCOPE, task.task_id, decision("yes", 1))
    pending = core.pending_completions(SCOPE)
    core.close()
    reopened = service(path)
    try:
        assert reopened.get(SCOPE, task.task_id) == approved
        assert reopened.pending_completions(SCOPE) == pending
        assert reopened.pending_completions(OTHER) == ()
        sink = Sink()
        sink.available = False
        with pytest.raises(TaskError, match="could not be delivered") as failure:
            reopened.deliver_pending(SCOPE, sink)
        assert failure.value.code == "delivery_unavailable"
        assert "Destination unavailable" not in str(failure.value)
        assert reopened.pending_completions(SCOPE) == pending
        sink.available = True
        assert reopened.deliver_pending(SCOPE, sink) == 1
        assert reopened.deliver_pending(SCOPE, sink) == 0
        assert tuple(sink.delivered) == (pending[0].completion_id,)
        assert reopened.audit(SCOPE, task.task_id)[-1].kind == "completion_delivered"
    finally:
        reopened.close()


def test_outbox_write_failure_rolls_back_every_decision_record(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = service(path)
    try:
        task = core.create(SCOPE, request())
        with sqlite3.connect(path) as fault:
            fault.execute(
                "CREATE TRIGGER fail_outbox BEFORE INSERT ON human_outbox "
                "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
            )
        command = decision("atomic", 1)
        with pytest.raises(sqlite3.DatabaseError):
            core.decide(SCOPE, task.task_id, command)
        assert core.get(SCOPE, task.task_id) == task
        assert len(core.audit(SCOPE, task.task_id)) == 1
        assert core.pending_completions(SCOPE) == ()
        with sqlite3.connect(path) as fault:
            assert fault.execute("SELECT COUNT(*) FROM human_commands").fetchone() == (0,)
            fault.execute("DROP TRIGGER fail_outbox")
        assert core.decide(SCOPE, task.task_id, command).status == "approved"
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()


def test_uncertain_completion_ack_retries_same_id_after_restart(tmp_path: Path) -> None:
    class UncertainSink(Sink):
        calls = 0

        def deliver(self, completion: Completion) -> None:
            super().deliver(completion)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Acknowledgement lost after durable acceptance")

    path = tmp_path / "tasks.db"
    core = service(path)
    task = core.create(SCOPE, request())
    core.decide(SCOPE, task.task_id, decision("yes", 1))
    sink = UncertainSink()
    with pytest.raises(TaskError):
        core.deliver_pending(SCOPE, sink)
    pending = core.pending_completions(SCOPE)
    core.close()
    core = service(path)
    try:
        assert core.pending_completions(SCOPE) == pending
        assert core.deliver_pending(SCOPE, sink) == 1
        assert sink.calls == 2
        assert len(sink.delivered) == 1
        assert next(iter(sink.delivered.values())) == pending[0]
        assert core.deliver_pending(SCOPE, sink) == 0
    finally:
        core.close()


@pytest.mark.parametrize("rule", ["any_one", "all_approvers"])
def test_two_sqlite_connections_race_without_lost_votes(tmp_path: Path, rule: str) -> None:
    path = tmp_path / "tasks.db"
    setup = service(path)
    task = setup.create(SCOPE, request(rule))
    setup.close()
    barrier = Barrier(2)

    def vote(actor: str) -> bool:
        core = service(path, Authorization(actor))
        try:
            barrier.wait(timeout=10)
            try:
                core.decide(SCOPE, task.task_id, decision(actor, 1))
                return True
            except TaskError:
                if rule == "any_one":
                    return False
                current = core.get(SCOPE, task.task_id)
                core.decide(SCOPE, task.task_id, decision(actor + "-retry", current.revision))
                return True
        finally:
            core.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(vote, ("alice", "bob")))
    core = service(path)
    try:
        final = core.get(SCOPE, task.task_id)
        assert final.status == "approved"
        assert sum(outcomes) == (1 if rule == "any_one" else 2)
        assert len(final.decisions) == (1 if rule == "any_one" else 2)
        assert len(core.pending_completions(SCOPE)) == 1
    finally:
        core.close()
