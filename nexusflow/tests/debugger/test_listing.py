"""Metadata-only owner discovery, contextual cursors and durable seek ordering."""

import base64
import json
import sqlite3

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_debugger.listing import decode_cursor, encode_cursor
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import (
    DebugError,
    DebugRecord,
    DebugSession,
    DebugStart,
)
from nexusflow.modules.durable_runtime.types import ExecutionPlan, ReleaseBinding, Step
from nexusflow.modules.identity_environments.types import Environment

SCOPE = Scope(organization_id="acme", environment_id="dev")


def record(name, *, actor="alice", scope=SCOPE, project="hr", created="2026-10-28T10:00:00+00:00"):
    request = DebugStart(
        operation_id=name,
        source_revision=1,
        workflow_id="flow",
        configuration_revision="cfg",
        input={"private": "never-list-this"},
        mode="real",
    )
    session = DebugSession(
        session_id="session-" + name,
        project_id=project,
        source_revision=1,
        workflow_id="flow",
        configuration_revision="cfg",
        definition_digest="sha256:" + "b" * 64,
        actor_id=actor,
        created_at=created,
        execution_id="nf-" + "a" * 32,
        temporal_run_id=None,
        state="dispatch_pending",
    )
    plan = ExecutionPlan(
        ReleaseBinding(
            scope.organization_id,
            scope.environment_id,
            session.session_id,
            "sha256:" + "a" * 64,
            "cfg",
        ),
        "flow",
        session.definition_digest,
        (Step("start", "start"), Step("end", "end")),
        input_json=json.dumps(request.input),
        debug=True,
    )
    return DebugRecord(request, session, Environment(scope, "namespace", "queue"), plan)


def test_newest_first_ties_seek_and_context_isolation(tmp_path):
    repo = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        for item in (
            record("a"),
            record("b"),
            record("new", created="2026-10-28T10:01:00+00:00"),
            record("foreign-owner", actor="bob"),
            record("foreign-project", project="other"),
            record("foreign-env", scope=Scope(organization_id="acme", environment_id="uat")),
            record("foreign-org", scope=Scope(organization_id="other", environment_id="dev")),
        ):
            repo.prepare(item)
        first = repo.list_sessions(SCOPE, "hr", "alice", limit=1)
        assert [s.session_id for s in first.items] == ["session-new"]
        second = repo.list_sessions(SCOPE, "hr", "alice", limit=1, cursor=first.next_cursor)
        assert [s.session_id for s in second.items] == ["session-b"]
        third = repo.list_sessions(SCOPE, "hr", "alice", limit=1, cursor=second.next_cursor)
        assert [s.session_id for s in third.items] == ["session-a"]
        assert third.next_cursor is None
        assert repo.list_sessions(SCOPE, "missing", "alice").items == ()
        assert [s.actor_id for s in repo.list_sessions(SCOPE, "hr", "bob").items] == ["bob"]
    finally:
        repo.close()


def test_discovery_never_reads_private_columns_or_full_records(tmp_path, monkeypatch):
    repo = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    repo.prepare(record("a"))
    repo.prepare(record("b"))
    forbidden = {"request", "plan", "placement", "checksum"}

    def authorizer(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table == "debug_sessions" and column in forbidden:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def unexpected(*args):
        raise AssertionError("Must not decode full snapshot")

    monkeypatch.setattr(repo, "get", unexpected)
    monkeypatch.setattr(repo, "_decode", unexpected)
    repo.connection.set_authorizer(authorizer)
    try:
        page = repo.list_sessions(SCOPE, "hr", "alice", limit=1)
        assert page.next_cursor
        assert "never-list-this" not in page.model_dump_json()
    finally:
        repo.connection.set_authorizer(None)
        repo.close()


def test_restart_and_acknowledgement_do_not_reorder_or_invalidate_cursor(tmp_path):
    path = tmp_path / "debug.db"
    repo = SQLiteDebugSessionRepository(path)
    repo.prepare(record("a"))
    repo.prepare(record("b"))
    first = repo.list_sessions(SCOPE, "hr", "alice", limit=1)
    repo.acknowledge(SCOPE, "hr", "session-a", "exact-run")
    repo.close()
    repo = SQLiteDebugSessionRepository(path)
    try:
        page = repo.list_sessions(SCOPE, "hr", "alice", cursor=first.next_cursor)
        assert [s.session_id for s in page.items] == ["session-a"]
        assert page.items[0].state == "started"
        assert page.items[0].temporal_run_id == "exact-run"
    finally:
        repo.close()


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.0, "1", None])
def test_invalid_limit(limit):
    repo = SQLiteDebugSessionRepository(":memory:")
    try:
        with pytest.raises(DebugError) as error:
            repo.list_sessions(SCOPE, "hr", "alice", limit=limit)
        assert error.value.code == "invalid_request"
    finally:
        repo.close()


def token(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": True},
        {"version": 2},
        {"extra": 1},
        {"actor_id": "bob"},
        {"project_id": "other"},
        {"organization_id": "other"},
        {"environment_id": "prod"},
        {"created_at": "2026-10-28T10:00:00Z"},
        {"created_at": "not-a-time"},
        {"created_at": "2026-10-28T10:00:00+01:00"},
        {"session_id": "a/b"},
    ],
)
def test_malformed_or_foreign_cursor(mutation):
    original = encode_cursor(SCOPE, "hr", "alice", record("a").session)
    raw = json.loads(base64.urlsafe_b64decode(original + "=" * (-len(original) % 4)))
    raw.update(mutation)
    with pytest.raises(DebugError) as error:
        decode_cursor(SCOPE, "hr", "alice", token(raw))
    assert error.value.code == "invalid_request"
    assert "other" not in str(error.value)


@pytest.mark.parametrize("cursor", ["", "a", "!", "a" * 2049, "e30=", "____", 4])
def test_invalid_cursor_encoding(cursor):
    with pytest.raises(DebugError) as error:
        decode_cursor(SCOPE, "hr", "alice", cursor)
    assert error.value.code == "invalid_request"


def test_cursor_duplicate_keys_are_rejected():
    data = b'{"version":1,"version":1}'
    with pytest.raises(DebugError):
        decode_cursor(SCOPE, "hr", "alice", base64.urlsafe_b64encode(data).decode().rstrip("="))


@pytest.mark.parametrize(
    "changed",
    [
        {"session_id": "different"},
        {"project_id": "different"},
        {"source_revision": 0},
        {"created_at": "2026-10-28T10:00:00Z"},
        {"state": "started"},
        {"state": "started", "temporal_run_id": "private-marker" * 256},
        {"state": "started", "temporal_run_id": ""},
        {"temporal_run_id": ""},
        {"extra-private": "never-list-this"},
    ],
)
def test_corrupted_public_metadata_fails_closed(tmp_path, changed):
    repo = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    saved = record("a")
    repo.prepare(saved)
    value = saved.session.model_dump(mode="json")
    value.update(changed)
    repo.connection.execute("UPDATE debug_sessions SET session=?", (json.dumps(value),))
    repo.connection.commit()
    try:
        with pytest.raises(DebugError) as error:
            repo.list_sessions(SCOPE, "hr", "alice")
        assert error.value.code == "unavailable"
        assert "never-list-this" not in str(error.value)
    finally:
        repo.close()


def test_closed_store_and_invalid_json_have_safe_errors(tmp_path):
    repo = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    repo.prepare(record("a"))
    repo.connection.execute("UPDATE debug_sessions SET session='not-json'")
    repo.connection.commit()
    with pytest.raises(DebugError, match="discovery is unavailable"):
        repo.list_sessions(SCOPE, "hr", "alice")
    repo.close()
    with pytest.raises(DebugError, match="discovery is unavailable"):
        repo.list_sessions(SCOPE, "hr", "alice")


def test_inserting_newer_session_does_not_repeat_previous_page(tmp_path):
    repo = SQLiteDebugSessionRepository(tmp_path / "debug.db")
    try:
        repo.prepare(record("a"))
        repo.prepare(record("b"))
        first = repo.list_sessions(SCOPE, "hr", "alice", limit=1)
        repo.prepare(record("new", created="2026-10-28T10:01:00+00:00"))
        rest = repo.list_sessions(SCOPE, "hr", "alice", cursor=first.next_cursor)
        assert [s.session_id for s in rest.items] == ["session-a"]
        assert repo.list_sessions(SCOPE, "hr", "alice").items[0].session_id == "session-new"
    finally:
        repo.close()
