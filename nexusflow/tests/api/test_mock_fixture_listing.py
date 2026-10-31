"""Signed bounded metadata discovery with real SQLite fixtures."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PATH = BASE + "/mock-fixtures"
PRIVATE = "never-list-private-fixture"
BODY = {"path": "/" + PRIVATE, "status_code": 200, "response_json_body": {"private": PRIVATE}}


@pytest.fixture
def connected(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    store = SQLiteHTTPMockScenarios(tmp_path / "fixtures.db")
    backend = replace(backend, mock_fixtures=store, mock_fixture_dev_scopes=(scope,))
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime
    store.close()


def write(client, connection, revision):
    response = client.put(
        BASE + f"/connections/{connection}/revisions/{revision}/mock-fixture", json=BODY
    )
    assert response.status_code == 201, response.text
    return response.json()


def private(response):
    assert PRIVATE not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_keyset_pages_are_exclusive_scoped_and_metadata_only(connected, monkeypatch):
    client, backend, _, runtime = connected
    expected = [write(client, c, r) for c, r in [("a", "v10"), ("a", "v2"), ("b", "v1")]]
    monkeypatch.setattr(
        backend.mock_fixtures, "get", Mock(side_effect=AssertionError("Private read"))
    )
    monkeypatch.setattr(runtime, "dispatcher", Mock(side_effect=AssertionError("Runtime access")))
    first = client.get(PATH + "?limit=2")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "items": expected[:2],
        "next_after_connection_id": "a",
        "next_after_connection_revision": "v2",
    }
    second = client.get(PATH + "?limit=2&after_connection_id=a&after_connection_revision=v2")
    assert second.json() == {
        "items": expected[2:],
        "next_after_connection_id": None,
        "next_after_connection_revision": None,
    }
    for response in (first, second):
        private(response)
    assert (
        backend.projects.list_projects(backend.mock_fixture_dev_scopes[0]) == ()
        and runtime.plans == {}
    )


def test_empty_listing_has_no_cursor(connected):
    client, _, _, _ = connected
    assert client.get(PATH).json() == {
        "items": [],
        "next_after_connection_id": None,
        "next_after_connection_revision": None,
    }


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=51",
        "limit=-1",
        "limit=1.0",
        "limit=%2B1",
        "limit=%201",
        "limit=١",
        "limit=1&limit=2",
        "unknown=" + PRIVATE,
        "after_connection_id=a",
        "after_connection_revision=v1",
        "after_connection_id=&after_connection_revision=v1",
        "after_connection_id=a&after_connection_revision=v1&after_connection_id=b",
        "after_connection_id=bad%2Fvalue&after_connection_revision=v1",
    ],
)
def test_invalid_pagination_is_private_and_never_lists(connected, monkeypatch, query):
    client, backend, _, _ = connected
    listing = Mock(side_effect=AssertionError("Invalid query touched provider"))
    monkeypatch.setattr(backend.mock_fixtures, "list_metadata", listing)
    response = client.get(PATH + "?" + query)
    assert response.status_code == 422, response.text
    private(response)
    listing.assert_not_called()


def test_fresh_revocation_blocks_provider_listing(connected, monkeypatch):
    client, backend, data, _ = connected
    write(client, "a", "v1")
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    listing = Mock(side_effect=AssertionError("Revoked provider access"))
    monkeypatch.setattr(backend.mock_fixtures, "list_metadata", listing)
    response = client.get(PATH)
    assert response.status_code == 403, response.text
    private(response)
    listing.assert_not_called()


def test_default_permission_and_explicit_enablement_gate_provider(api):  # noqa: F811
    client, backend, data, _ = api
    assert client.get(PATH).status_code == 403
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    assert client.get(PATH).status_code == 403
    with TestClient(create_app(replace(backend, mock_fixture_dev_scopes=(scope,)))) as enabled:
        enabled.headers.update(client.headers)
        assert enabled.get(PATH).status_code == 503


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_nondev_listing_always_forbidden(connected, environment):
    client, backend, data, _ = connected
    identities, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    identities.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    with TestClient(create_app(replace(backend, mock_fixture_dev_scopes=(target,)))) as other:
        other.headers.update(client.headers)
        assert other.get(PATH.replace("/dev/", f"/{environment}/")).status_code == 403


def test_bad_identity_and_foreign_scope_are_denied(connected):
    client, _, _, _ = connected
    assert client.get(PATH.replace("/acme/", "/foreign/")).status_code == 401
    client.headers["authorization"] = "Bearer private-invalid-token"
    response = client.get(PATH)
    assert response.status_code == 401
    private(response)


def test_corrupted_metadata_and_private_provider_errors_fail_closed(connected, monkeypatch):
    client, backend, _, _ = connected
    write(client, "a", "v1")
    backend.mock_fixtures._db.execute("UPDATE http_mock_scenarios SET fixture_digest=?", (PRIVATE,))
    backend.mock_fixtures._db.commit()
    response = client.get(PATH)
    assert response.status_code == 503, response.text
    private(response)
    monkeypatch.setattr(backend.mock_fixtures, "list_metadata", Mock(side_effect=OSError(PRIVATE)))
    response = client.get(PATH)
    assert response.status_code == 503
    private(response)
