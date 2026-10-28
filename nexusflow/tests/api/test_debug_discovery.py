"""Signed owner-only discovery, with no runtime or private snapshot loading."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts import Scope
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, debugging  # noqa: F401

OTHER = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def discovered(debugging):  # noqa: F811
    client, backend, data, runtime = debugging
    sessions = []
    for operation in ("older", "newer"):
        response = client.post(
            PATH,
            json=dict(PAYLOAD, operation_id=operation, input={"private": "hidden-discovery-input"}),
        )
        assert response.status_code == 202, response.text
        sessions.append(response.json())
    return client, backend, data, runtime, sessions


def test_discovery_pages_metadata_without_runtime_source_or_writes(discovered, monkeypatch):
    client, backend, _, runtime, sessions = discovered
    fail = Mock(side_effect=AssertionError("Discovery must not load runtime/source"))
    monkeypatch.setattr(runtime, "snapshot", fail)
    monkeypatch.setattr(runtime, "control", fail)
    monkeypatch.setattr(backend.projects, "revision", fail)
    monkeypatch.setattr(backend.projects, "project", fail)
    backend.configurations.available = False
    changes = backend.debug_sessions.connection.total_changes
    first = client.get(PATH, params={"limit": 1})
    assert first.status_code == 200, first.text
    assert first.json()["items"] == [sessions[1]]
    assert first.headers["cache-control"] == "no-store"
    cursor = first.json()["next_cursor"]
    assert cursor
    second = client.get(PATH, params={"limit": 1, "cursor": cursor})
    assert second.status_code == 200, second.text
    assert second.json() == {"items": [sessions[0]], "next_cursor": None}
    assert "hidden-discovery-input" not in first.text + second.text
    assert "plan" not in first.json() and "input" not in first.json()["items"][0]
    assert backend.debug_sessions.connection.total_changes == changes
    fail.assert_not_called()
    assert client.get(BASE + "/projects/absent/debug-sessions").json() == {
        "items": [],
        "next_cursor": None,
    }


def test_discovery_uses_verified_owner_and_binds_cursor(discovered):
    client, backend, data, _, sessions = discovered
    repository, scope, grant, _, token = data
    cursor = client.get(PATH, params={"limit": 1}).json()["next_cursor"]
    repository.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    repository.save_grant(replace(grant, grant_id="other", principal_id=OTHER), expected_revision=0)
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-subject")
    assert client.get(PATH, params={"actor_id": sessions[0]["actor_id"]}).json() == {
        "items": [],
        "next_cursor": None,
    }
    assert client.get(PATH, params={"cursor": cursor}).status_code == 422
    own = client.post(PATH, json=dict(PAYLOAD, operation_id="other-session"))
    assert own.status_code == 202, own.text
    assert client.get(PATH).json()["items"] == [own.json()]
    client.headers["authorization"] = "Bearer " + token()
    assert len(client.get(PATH).json()["items"]) == 2
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(PATH).status_code == 403


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_discovery_nondevelopment_permission_before_missing_provider(discovered, environment):
    original, backend, data, _, _ = discovered
    repository, _, grant, _, _ = data
    repository.save_grant(
        replace(grant, scope=Scope(organization_id="acme", environment_id=environment)),
        expected_revision=0,
    )
    with TestClient(
        create_app(replace(backend, debug_sessions=None, debug_runtime=None)),
        raise_server_exceptions=False,
    ) as client:
        client.headers.update(original.headers)
        assert client.get(PATH.replace("/dev", "/" + environment)).status_code == 403


def test_discovery_missing_provider_authorizes_first(api):  # noqa: F811
    client, _, data, _ = api
    assert client.get(PATH).status_code == 503
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(PATH).status_code == 403


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"cursor": "x" * 2049}])
def test_discovery_transport_limits(discovered, query):
    assert discovered[0].get(PATH, params=query).status_code == 422


def test_discovery_invalid_cursor_and_project_context(discovered):
    client, _, _, _, _ = discovered
    assert client.get(PATH, params={"cursor": "not-a-cursor"}).status_code == 422
    cursor = client.get(PATH, params={"limit": 1}).json()["next_cursor"]
    assert (
        client.get(BASE + "/projects/other/debug-sessions", params={"cursor": cursor}).status_code
        == 422
    )


def test_discovery_unavailable_storage_safe(discovered, monkeypatch):
    client, backend, _, _, _ = discovered
    monkeypatch.setattr(
        backend.debug_sessions,
        "list_sessions",
        Mock(side_effect=OSError("private-database-detail")),
    )
    response = client.get(PATH)
    assert response.status_code == 503
    assert "private-database-detail" not in response.text


def test_discovery_typed_authenticated_schema(discovered):
    client, _, _, _, _ = discovered
    schema = client.get(BASE + "/schema")
    assert schema.status_code == 200, schema.text
    operation = next(
        value["get"]
        for key, value in schema.json()["paths"].items()
        if key.endswith("/projects/{project_id}/debug-sessions")
    )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "DebugSessionPage"
    )
