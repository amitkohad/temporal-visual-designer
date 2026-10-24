"""Inbox acceptance against persisted approval snapshots, independent of HTTP."""

from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask, DecisionRequest, TaskError
from nexusflow.tests.human_tasks.test_acceptance import Authorization, Directory

DEV = Scope(organization_id="organization", environment_id="dev")
UAT = Scope(organization_id="organization", environment_id="uat")
FOREIGN = Scope(organization_id="foreign", environment_id="dev")


def request(invocation: str, run: str = "purchase") -> CreateTask:
    return CreateTask(
        run_id=run,
        node_invocation_id=invocation,
        definition_revision="release-1",
        rule="all_approvers",
        group_ids=("purchasing",),
    )


def vote(command: str, revision: int, decision: str = "approve") -> DecisionRequest:
    return DecisionRequest.model_validate(
        {"command_id": command, "expected_revision": revision, "decision": decision}
    )


def test_membership_filter_precedes_pagination_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    auth, directory = Authorization(), Directory()
    core = TaskService(path, auth, directory)
    expected = []
    try:
        for index in range(12):
            # Most rows belong to another approver and must not consume page slots.
            directory.resolved = ("alice",) if index % 3 == 0 else ("outsider",)
            task = core.create(DEV, request(f"node-{index}"))
            if index % 3 == 0:
                expected.append(task)
        directory.resolved = ("alice",)
        core.create(UAT, request("foreign-environment"))
        core.create(FOREIGN, request("foreign-organization"))
    finally:
        core.close()

    directory.resolved = ("replacement",)
    core = TaskService(path, auth, directory)
    try:
        first = core.list_tasks(DEV, limit=2)
        assert first.items == tuple(expected[:2])
        assert first.next_offset == 2
        second = core.list_tasks(DEV, limit=2, offset=first.next_offset)
        assert second.items == tuple(expected[2:])
        assert second.next_offset is None
        assert core.list_tasks(DEV, offset=4).items == ()
        assert core.list_tasks(UAT).items[0].scope == UAT
        assert core.list_tasks(FOREIGN).items[0].scope == FOREIGN
        assert auth.calls[-1] == (FOREIGN, "task.read", "tasks")
        assert directory.calls == 14
        auth.actor = "replacement"
        assert core.list_tasks(DEV).items == ()
        auth.actor = "outsider"
        assert len(core.list_tasks(DEV).items) == 8
        auth.allowed = False
        with pytest.raises(TaskError) as failure:
            core.list_tasks(DEV)
        assert failure.value.code == "forbidden"
    finally:
        core.close()


def test_status_and_run_filters_use_stored_decisions_and_receipts(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    auth = Authorization()
    core = TaskService(path, auth, Directory())
    try:
        pending = core.create(DEV, request("pending"))
        approved = core.create(DEV, request("approved"))
        rejected = core.create(DEV, request("rejected", "onboarding"))
        command = vote("alice-approval", 1)
        receipt = core.decide(DEV, approved.task_id, command)
        auth.actor = "bob"
        terminal = core.decide(DEV, approved.task_id, vote("bob-approval", 2))
        core.decide(DEV, rejected.task_id, vote("rejection", 1, "reject"))
    finally:
        core.close()

    auth.actor = "alice"
    core = TaskService(path, auth)
    try:
        assert len(core.list_tasks(DEV).items) == 3
        assert core.list_tasks(DEV, status="pending").items == (pending,)
        assert core.list_tasks(DEV, status="approved").items == (terminal,)
        assert core.list_tasks(DEV, status="rejected").items[0].task_id == rejected.task_id
        assert len(core.list_tasks(DEV, run_id="purchase").items) == 2
        assert core.list_tasks(DEV, run_id="purchase", status="rejected").items == ()
        assert core.list_tasks(DEV, run_id="missing").items == ()
        # Retrying the old command returns its committed intermediate response,
        # while the current task remains terminal, with no duplicate audit/outbox.
        assert core.decide(DEV, approved.task_id, command) == receipt
        assert core.get(DEV, approved.task_id) == terminal
        assert len(core.audit(DEV, approved.task_id)) == 3
        assert len(core.pending_completions(DEV)) == 2
        with pytest.raises(TaskError) as failure:
            core.create(DEV, request("new-task"))
        assert failure.value.code == "assignment_unavailable"
    finally:
        core.close()


@pytest.mark.parametrize("actor", ["", "invalid/actor", "a" * 129])
def test_inbox_rejects_invalid_authorization_identity(tmp_path: Path, actor: str) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization(actor))
    try:
        with pytest.raises(TaskError) as failure:
            core.list_tasks(DEV)
        assert failure.value.code == "forbidden"
    finally:
        core.close()
