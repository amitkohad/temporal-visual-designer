"""Inbox selection must apply trusted assignment and scope before pagination."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import TaskError
from nexusflow.tests.human_tasks.test_acceptance import (
    OTHER,
    SCOPE,
    Authorization,
    Directory,
    decision,
    request,
)


def test_scoped_membership_precedes_pagination_and_filters(tmp_path: Path) -> None:
    auth, directory = Authorization(), Directory(("bob",))
    core = TaskService(tmp_path / "tasks.db", auth, directory)
    try:
        core.create(SCOPE, request())  # Hidden oldest row must not consume the first page.
        directory.resolved = ("alice",)
        first = core.create(SCOPE, request().model_copy(update={"node_invocation_id": "two"}))
        second = core.create(SCOPE, request().model_copy(update={"node_invocation_id": "three"}))
        third = core.create(
            SCOPE,
            request().model_copy(update={"run_id": "run-other", "node_invocation_id": "four"}),
        )
        core.create(OTHER, request())
        core.decide(SCOPE, second.task_id, decision("approve", 1))
        page = core.list_tasks(SCOPE, limit=1)
        assert page.items == (first,)
        assert page.next_offset == 1
        page2 = core.list_tasks(SCOPE, limit=1, offset=page.next_offset)
        assert page2.items[0].task_id == second.task_id
        assert page2.next_offset == 2
        last = core.list_tasks(SCOPE, limit=1, offset=2)
        assert last.items == (third,)
        assert last.next_offset is None
        assert tuple(item.task_id for item in core.list_tasks(SCOPE, status="approved").items) == (
            second.task_id,
        )
        assert core.list_tasks(SCOPE, run_id="run-other").items == (third,)
        assert core.list_tasks(SCOPE, status="approved", run_id="run-other").items == ()
        assert core.list_tasks(SCOPE, offset=10000).items == ()
        assert auth.calls[-1] == (SCOPE, "task.read", "tasks")
    finally:
        core.close()


def test_group_snapshot_fresh_authorization_and_read_only_host(tmp_path: Path) -> None:
    auth, directory = Authorization(), Directory(("alice", "bob"))
    path = tmp_path / "tasks.db"
    core = TaskService(path, auth, directory)
    task = core.create(SCOPE, request())
    core.close()
    directory.resolved = ("charlie",)
    reader = TaskService(path, auth)
    try:
        assert reader.list_tasks(SCOPE).items == (task,)
        auth.actor = "bob"
        assert reader.list_tasks(SCOPE).items == (task,)
        auth.actor = "charlie"
        assert reader.list_tasks(SCOPE).items == ()
        auth.actor = "alice"
        with pytest.raises(TaskError) as missing:
            reader.create(SCOPE, request().model_copy(update={"node_invocation_id": "new"}))
        assert missing.value.code == "assignment_unavailable"
        assert (
            reader.decide(SCOPE, task.task_id, decision("read-host-decision", 1)).status
            == "approved"
        )
        auth.allowed = False
        with pytest.raises(TaskError) as revoked:
            reader.list_tasks(SCOPE)
        assert revoked.value.code == "forbidden"
    finally:
        reader.close()


@pytest.mark.parametrize(
    "filters",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"limit": "1"},
        {"limit": 1.0},
        {"offset": False},
        {"offset": -1},
        {"offset": 10001},
        {"offset": "0"},
        {"status": "unknown"},
        {"status": 1},
        {"run_id": "bad/run"},
        {"run_id": 1},
    ],
)
def test_invalid_inbox_filters_are_rejected(tmp_path: Path, filters: dict[str, Any]) -> None:
    core = TaskService(tmp_path / "tasks.db", Authorization())
    try:
        with pytest.raises(TaskError) as invalid:
            core.list_tasks(SCOPE, **filters)
        assert invalid.value.code == "invalid_input"
    finally:
        core.close()


def test_equal_creation_times_have_stable_id_order(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    core = TaskService(path, Authorization(), Directory())
    try:
        tasks = [
            core.create(SCOPE, request().model_copy(update={"node_invocation_id": f"node-{index}"}))
            for index in range(3)
        ]
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE human_tasks SET view=json_set(view,'$.created_at',?)",
                ("2026-01-01T00:00:00+00:00",),
            )
        expected = sorted(task.task_id for task in tasks)
        for offset, task_id in enumerate(expected):
            assert core.list_tasks(SCOPE, limit=1, offset=offset).items[0].task_id == task_id
    finally:
        core.close()
