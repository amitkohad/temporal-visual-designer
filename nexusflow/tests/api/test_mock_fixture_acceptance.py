"""Signed API to real SQLite/mock execution; no cloud or default worker claim."""

import asyncio
import socket
import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.connectors.activity import HTTPJSONActivityAdapter
from nexusflow.modules.connectors.activity_types import HTTPActivityError
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_execution import HTTPMockExecutor
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PRIVATE = "private-admin-fixture-sentinel"
PATH = BASE + "/connections/hr/revisions/mock-v1/mock-fixture"
BODY = {
    "method": "POST",
    "path": "/employees?synthetic=" + PRIVATE,
    "json_body": {"employee": PRIVATE, "nested": [True, None, "é"]},
    "status_code": 201,
    "response_json_body": {"synthetic_response": PRIVATE},
}
METADATA_FIELDS = {
    "scope",
    "connection_id",
    "connection_revision",
    "fixture_digest",
    "stored_bytes",
}


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, connection_id):
        self.calls.append((scope, permission, connection_id))
        assert permission == "worker.execute" and connection_id == "hr"
        if self.revoked:
            raise PermissionError(PRIVATE)
        return "synthetic-worker"


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Mock acceptance must not access network"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    yield forbidden
    forbidden.assert_not_called()


@pytest.fixture
def enabled(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "admin-fixtures.sqlite3"
    store = SQLiteHTTPMockScenarios(database)
    backend = replace(backend, mock_fixtures=store, mock_fixture_dev_scopes=(scope,))
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime, database
    store.close()


def metadata(response, status=200):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert set(value) == METADATA_FIELDS
    assert PRIVATE not in response.text
    return value


def inputs(fixture_digest):
    return {
        "connection_id": "hr",
        "connection_revision": "mock-v1",
        "execution_mode": "mock",
        "fixture_digest": fixture_digest,
        "method": BODY["method"],
        "path": BODY["path"],
        "json_body": BODY["json_body"],
    }


def test_signed_write_survives_restart_and_executes_through_actual_mock_adapter(enabled):
    client, backend, data, runtime, database = enabled
    saved = metadata(client.put(PATH, json=BODY), 201)
    assert metadata(client.get(PATH)) == saved
    scope = data[1]
    assert saved["scope"] == scope.model_dump(mode="json")
    assert saved["connection_id"] == "hr" and saved["connection_revision"] == "mock-v1"
    assert backend.projects.list_projects(scope) == ()
    assert runtime.plans == {} and runtime.placements == []

    backend.mock_fixtures.close()
    reopened = SQLiteHTTPMockScenarios(database)
    try:
        restarted_backend = replace(backend, mock_fixtures=reopened)
        with TestClient(create_app(restarted_backend), raise_server_exceptions=False) as restarted:
            restarted.headers.update(client.headers)
            assert metadata(restarted.get(PATH)) == saved
            assert metadata(restarted.put(PATH, json=BODY), 201) == saved
        worker = Worker()
        live = Mock()
        live.execute.side_effect = AssertionError("No live fallback")
        adapter = HTTPJSONActivityAdapter(
            live=live,
            mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(worker, (scope,)), reopened),
        )
        result = asyncio.run(adapter.execute(scope, "operation-1", inputs(saved["fixture_digest"])))
        assert result.model_dump() == {
            "operation_id": "operation-1",
            "status_code": 201,
            "execution_mode": "mock",
        }
        assert PRIVATE not in result.model_dump_json()
        assert len(worker.calls) == 2
        live.execute.assert_not_called()
    finally:
        reopened.close()


def test_canonical_retry_conflict_and_response_mutation_preserve_fixture(enabled):
    client, _, _, _, _ = enabled
    saved = metadata(client.put(PATH, json=BODY), 201)
    reordered = {**BODY, "json_body": {"nested": [True, None, "é"], "employee": PRIVATE}}
    assert metadata(client.put(PATH, json=reordered), 201) == saved
    saved["fixture_digest"] = "changed-client-metadata"
    current = metadata(client.get(PATH))
    assert current["fixture_digest"] != saved["fixture_digest"]
    changed = client.put(PATH, json={**BODY, "response_json_body": {"changed": PRIVATE}})
    assert changed.status_code == 409, changed.text
    assert PRIVATE not in changed.text
    assert metadata(client.get(PATH)) == current


def test_revoked_admin_cannot_read_or_retry_persisted_fixture(enabled, monkeypatch):
    client, backend, data, _, _ = enabled
    metadata(client.put(PATH, json=BODY), 201)
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    forbidden = Mock(side_effect=AssertionError("Storage accessed after revocation"))
    monkeypatch.setattr(backend.mock_fixtures, "get", forbidden)
    monkeypatch.setattr(backend.mock_fixtures, "put", forbidden)
    for response in (client.get(PATH), client.put(PATH, json=BODY)):
        assert response.status_code == 403, response.text
        assert PRIVATE not in response.text
    forbidden.assert_not_called()


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_admin_and_explicit_profile_do_not_enable_nondev_fixture_management(enabled, environment):
    client, backend, data, _, _ = enabled
    identities, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    identities.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    with TestClient(
        create_app(replace(backend, mock_fixture_dev_scopes=(scope, target))),
        raise_server_exceptions=False,
    ) as other:
        other.headers.update(client.headers)
        route = PATH.replace("/dev/", f"/{environment}/")
        for response in (other.put(route, json=BODY), other.get(route)):
            assert response.status_code == 403, response.text
    assert metadata(client.put(PATH, json=BODY), 201)["scope"] == scope.model_dump(mode="json")


def test_fixture_presence_does_not_enable_unconfigured_host_or_cross_organization(enabled):
    client, backend, _, _, _ = enabled
    metadata(client.put(PATH, json=BODY), 201)
    with TestClient(
        create_app(replace(backend, mock_fixture_dev_scopes=())), raise_server_exceptions=False
    ) as disabled:
        disabled.headers.update(client.headers)
        for response in (disabled.get(PATH), disabled.put(PATH, json=BODY)):
            assert response.status_code == 403, response.text
    assert client.get(PATH.replace("/acme/", "/foreign/")).status_code == 401


def test_corrupt_persisted_fixture_is_private_api_failure_and_cannot_execute(enabled):
    client, backend, data, _, database = enabled
    saved = metadata(client.put(PATH, json=BODY), 201)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE http_mock_scenarios SET fixture_digest=?", (PRIVATE,))
    response = client.get(PATH)
    assert response.status_code == 503, response.text
    assert PRIVATE not in response.text
    retry = client.put(PATH, json=BODY)
    assert retry.status_code == 503, retry.text
    assert PRIVATE not in retry.text
    scope = data[1]
    adapter = HTTPJSONActivityAdapter(
        mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(Worker(), (scope,)), backend.mock_fixtures)
    )
    with pytest.raises(HTTPActivityError) as caught:
        asyncio.run(adapter.execute(scope, "operation-1", inputs(saved["fixture_digest"])))
    assert caught.value.code == "unavailable"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
