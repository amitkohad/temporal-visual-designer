"""Explicit debugger composition preserves defaults and owns session resources."""

import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps import local_bootstrap
from nexusflow.apps.api import create_app
from nexusflow.apps.api_backend import TemporalRuntimeGateway
from nexusflow.apps.local_bootstrap import initialize, issue_token, open_backend
from nexusflow.apps.local_debug import enable_debugger
from nexusflow.contracts.models import Scope
from nexusflow.modules.durable_runtime.temporal_starts import SQLiteDispatchJournal
from nexusflow.tests.local.test_bootstrap import BASE, Runtime


def gateway(tmp_path):
    async def clients(placement):
        raise AssertionError("No server should be contacted by bootstrap")

    return TemporalRuntimeGateway(clients, SQLiteDispatchJournal(tmp_path / "owned-dispatch.db"))


def test_disabled_keeps_existing_backend_and_does_not_create_debug_state(tmp_path):
    initialize(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        assert backend.debug_sessions is backend.debug_runtime is None
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        response = client.post(
            BASE + "/projects/welcome/debug-sessions",
            json={
                "operation_id": "try",
                "source_revision": 1,
                "workflow_id": "welcome",
                "configuration_revision": "local-v1",
                "mode": "real",
            },
        )
        assert response.status_code == 503
    assert not (tmp_path / "debugger.json").exists()
    assert not (tmp_path / "debug-sessions.db").exists()


def test_enabled_provider_owns_closes_and_reopens_original_session_store(tmp_path):
    initialize(tmp_path)
    profile = enable_debugger(tmp_path)
    runtime = gateway(tmp_path)
    try:
        with open_backend(tmp_path, runtime) as backend:
            repo = backend.debug_sessions
            assert repo is not None and backend.debug_runtime is not None
            assert backend.debug_runtime.clients is runtime.clients
            assert backend.debug_runtime.journal is runtime.journal
            assert repo.operation(profile.scope, "welcome", "not-created") is None
        with pytest.raises(sqlite3.ProgrammingError):
            repo.operation(profile.scope, "welcome", "not-created")
        with open_backend(tmp_path, runtime) as reopened:
            assert reopened.debug_sessions is not repo
            assert (
                reopened.debug_sessions.operation(profile.scope, "welcome", "not-created") is None
            )
    finally:
        runtime.journal.close()


def test_enabled_rejects_unsupported_runtime_before_writable_backend(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_debugger(tmp_path)
    constructor = Mock(side_effect=AssertionError("Writable providers must not open"))
    monkeypatch.setattr(local_bootstrap, "SQLiteProjectRepository", constructor)
    with pytest.raises(ValueError, match="owned Temporal"):
        with open_backend(tmp_path, Runtime()):
            raise AssertionError("Must not open")
    constructor.assert_not_called()


def test_missing_debug_state_is_not_recreated_on_open(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_debugger(tmp_path)
    (tmp_path / "debug-sessions.db").unlink()
    constructor = Mock(side_effect=AssertionError("Writable providers must not open"))
    monkeypatch.setattr(local_bootstrap, "SQLiteProjectRepository", constructor)
    with pytest.raises(ValueError):
        with open_backend(tmp_path, Runtime()):
            raise AssertionError("Must not open")
    constructor.assert_not_called()
    assert not (tmp_path / "debug-sessions.db").exists()


def test_resource_closed_if_later_backend_initialization_fails(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_debugger(tmp_path)
    runtime = gateway(tmp_path)
    from nexusflow.modules.designer_debugger import repository

    real = repository.SQLiteDebugSessionRepository
    captured = []

    def capture(*args, **kwargs):
        instance = real(*args, **kwargs)
        captured.append(instance)
        return instance

    monkeypatch.setattr(repository, "SQLiteDebugSessionRepository", capture)
    monkeypatch.setattr(
        local_bootstrap, "SQLitePackageImportRepository", Mock(side_effect=OSError("fail"))
    )
    try:
        with pytest.raises(OSError):
            with open_backend(tmp_path, runtime):
                raise AssertionError("Must not yield")
        assert len(captured) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            captured[0].operation(
                Scope(organization_id="local-demo", environment_id="dev"), "welcome", "none"
            )
    finally:
        runtime.journal.close()


def test_enabling_and_reopening_never_restore_revoked_author(tmp_path):
    initialize(tmp_path)
    scope = Scope(organization_id="local-demo", environment_id="dev")
    with open_backend(tmp_path, Runtime()) as backend:
        backend.identity.repository.revoke_grant(scope, "local-author", expected_revision=1)
    enable_debugger(tmp_path)
    runtime = gateway(tmp_path)
    try:
        with open_backend(tmp_path, runtime) as backend, TestClient(create_app(backend)) as client:
            client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
            response = client.get(BASE + "/projects/welcome/debug-sessions/none")
            assert response.status_code == 403
    finally:
        runtime.journal.close()
