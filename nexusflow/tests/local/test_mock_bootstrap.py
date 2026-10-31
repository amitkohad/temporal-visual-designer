"""Actual optional fixture store lifecycle and signed local administrator policy."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps import local_bootstrap
from nexusflow.apps.api import create_app
from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID, initialize, issue_token, open_backend
from nexusflow.apps.local_mocks import enable_mock_fixtures
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.tests.local.test_bootstrap import BASE, SCOPE, Runtime

PATH = BASE + "/connections/hr/revisions/v1/mock-fixture"
BODY = {
    "method": "POST",
    "path": "/private-fixture-path",
    "json_body": {"employee": "private-fixture-value"},
    "status_code": 201,
    "response_json_body": {"result": "private-fixture-value"},
}


def test_disabled_default_never_creates_profile_or_provider(tmp_path):
    initialize(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        assert backend.mock_fixtures is None
        assert backend.mock_fixture_dev_scopes == ()
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.put(PATH, json=BODY).status_code == 403
        assert client.get(BASE + "/mock-fixtures").status_code == 403
    assert not (tmp_path / "http-mock-fixtures.json").exists()
    assert not (tmp_path / "http-mock-fixtures.db").exists()


def test_enabled_signed_put_get_list_persist_after_owned_store_close(tmp_path, monkeypatch):
    initialize(tmp_path)
    profile = enable_mock_fixtures(tmp_path)
    runtime = Runtime()
    monkeypatch.setattr(runtime, "dispatcher", Mock(side_effect=AssertionError("Runtime access")))
    with open_backend(tmp_path, runtime) as backend, TestClient(create_app(backend)) as client:
        store = backend.mock_fixtures
        assert store is not None
        assert backend.mock_fixture_dev_scopes == (profile.scope,)
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        response = client.put(PATH, json=BODY)
        assert response.status_code == 201, response.text
        metadata = response.json()
        assert "private-fixture" not in response.text
        assert client.get(PATH).json() == metadata
        assert client.get(BASE + "/mock-fixtures").json()["items"] == [metadata]
        assert backend.connections is None
    with pytest.raises(HTTPMockStorageError) as caught:
        store.list_metadata(SCOPE)
    assert caught.value.code == "unavailable"
    with open_backend(tmp_path, runtime) as reopened, TestClient(create_app(reopened)) as client:
        assert reopened.mock_fixtures is not store
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(PATH).json() == metadata
        assert client.put(PATH, json=BODY).json() == metadata
        assert client.get(BASE + "/mock-fixtures").json()["items"] == [metadata]


def test_revoked_administrator_is_not_restored_by_enable_or_reopen(tmp_path):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend:
        backend.identity.repository.revoke_grant(
            SCOPE, "local-mock-fixture-admin", expected_revision=1
        )
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        for response in (
            client.put(PATH, json=BODY),
            client.get(PATH),
            client.get(BASE + "/mock-fixtures"),
        ):
            assert response.status_code == 403
        assert client.get(BASE + "/projects").status_code == 200


def test_enable_does_not_restore_author_permission(tmp_path):
    initialize(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend:
        backend.identity.repository.revoke_grant(SCOPE, "local-author", expected_revision=1)
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(BASE + "/projects").status_code == 403
        assert client.put(PATH, json=BODY).status_code == 201


def test_reenable_and_reopen_do_not_restore_membership(tmp_path):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend:
        backend.identity.repository.remove_membership("local-demo", TENANT_ID, "user", USER_ID)
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        assert client.get(BASE + "/mock-fixtures").status_code == 403
        assert client.put(PATH, json=BODY).status_code == 403


def test_dev_profile_adds_no_uat_or_prod_grants(tmp_path):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    with open_backend(tmp_path, Runtime()) as backend, TestClient(create_app(backend)) as client:
        client.headers["authorization"] = "Bearer " + issue_token(tmp_path)
        for environment in ("uat", "prod"):
            path = BASE.replace("/dev", "/" + environment)
            assert client.get(path + "/mock-fixtures").status_code == 403


def test_invalid_profile_is_checked_before_writable_providers(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    (tmp_path / "http-mock-fixtures.db").unlink()
    constructor = Mock(side_effect=AssertionError("Writable providers must not open"))
    monkeypatch.setattr(local_bootstrap, "SQLiteProjectRepository", constructor)
    with pytest.raises(ValueError):
        with open_backend(tmp_path, Runtime()):
            raise AssertionError("Must not yield")
    constructor.assert_not_called()
    assert not (tmp_path / "http-mock-fixtures.db").exists()


def test_fixture_store_closed_if_api_backend_construction_fails(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    from nexusflow.modules.connectors import mock_repository

    original = mock_repository.SQLiteHTTPMockScenarios
    captured = []

    def capture(*args, **kwargs):
        store = original(*args, **kwargs)
        captured.append(store)
        return store

    monkeypatch.setattr(mock_repository, "SQLiteHTTPMockScenarios", capture)
    monkeypatch.setattr(local_bootstrap, "ApiBackend", Mock(side_effect=RuntimeError("failure")))
    with pytest.raises(RuntimeError):
        with open_backend(tmp_path, Runtime()):
            raise AssertionError("Must not yield")
    assert len(captured) == 1
    with pytest.raises(HTTPMockStorageError):
        captured[0].list_metadata(SCOPE)
