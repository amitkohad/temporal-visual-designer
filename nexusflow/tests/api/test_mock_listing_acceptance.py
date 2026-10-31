"""Signed metadata discovery to actual persistent mock execution; local only."""

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

PRIVATE = "private-listing-fixture-sentinel"
LIST = BASE + "/mock-fixtures"
BODY = {
    "method": "POST",
    "path": "/employees?synthetic=" + PRIVATE,
    "json_body": {"employee": PRIVATE},
    "status_code": 201,
    "response_json_body": {"synthetic_response": PRIVATE},
}
FIELDS = {"scope", "connection_id", "connection_revision", "fixture_digest", "stored_bytes"}


class Worker:
    def authorize(self, scope, permission, connection_id):
        assert permission == "worker.execute" and connection_id == "hr"
        return "synthetic-worker"


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Mock discovery must not access network"))
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
    database = tmp_path / "listing-fixtures.sqlite3"
    store = SQLiteHTTPMockScenarios(database)
    backend = replace(backend, mock_fixtures=store, mock_fixture_dev_scopes=(scope,))
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime, database
    store.close()


def path(connection_id="hr", revision="v1"):
    return BASE + f"/connections/{connection_id}/revisions/{revision}/mock-fixture"


def write(client, connection_id="hr", revision="v1"):
    response = client.put(path(connection_id, revision), json=BODY)
    assert response.status_code == 201, response.text
    assert PRIVATE not in response.text
    return response.json()


def page(client, **params):
    response = client.get(LIST, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text
    result = response.json()
    assert set(result) == {"items", "next_after_connection_id", "next_after_connection_revision"}
    assert all(set(item) == FIELDS for item in result["items"])
    return result


def cursor(result):
    return {
        "after_connection_id": result["next_after_connection_id"],
        "after_connection_revision": result["next_after_connection_revision"],
    }


def keys(result):
    return [(item["connection_id"], item["connection_revision"]) for item in result["items"]]


def adapter(store, scope):
    live = Mock()
    live.execute.side_effect = AssertionError("No live fallback")
    return HTTPJSONActivityAdapter(
        live=live, mock=HTTPMockExecutor(ExplicitDevHTTPMockPolicy(Worker(), (scope,)), store)
    ), live


def inputs(metadata):
    return {
        "connection_id": metadata["connection_id"],
        "connection_revision": metadata["connection_revision"],
        "execution_mode": "mock",
        "fixture_digest": metadata["fixture_digest"],
        "method": BODY["method"],
        "path": BODY["path"],
        "json_body": BODY["json_body"],
    }


def test_signed_discovery_resumes_after_reopen_and_selected_pin_executes(enabled):
    client, backend, data, runtime, database = enabled
    expected = [("alpha", "v1"), ("hr", "v1"), ("hr", "v10"), ("hr", "v2"), ("zeta", "v1")]
    saved = {key: write(client, *key) for key in reversed(expected)}
    first = page(client, limit=2)
    assert keys(first) == expected[:2]
    assert first["items"] == [saved[key] for key in expected[:2]]
    backend.mock_fixtures.close()
    reopened = SQLiteHTTPMockScenarios(database)
    try:
        with TestClient(
            create_app(replace(backend, mock_fixtures=reopened)), raise_server_exceptions=False
        ) as restarted:
            restarted.headers.update(client.headers)
            second = page(restarted, limit=2, **cursor(first))
            assert keys(second) == expected[2:4]
            final = page(restarted, limit=2, **cursor(second))
            assert keys(final) == expected[4:]
            assert final["next_after_connection_id"] is None
            assert final["next_after_connection_revision"] is None
            selected = second["items"][0]
            verified = restarted.get(path("hr", "v10"))
            assert verified.status_code == 200 and verified.json() == selected
        actual, live = adapter(reopened, data[1])
        output = asyncio.run(actual.execute(data[1], "selected-operation", inputs(selected)))
        assert output.model_dump() == {
            "operation_id": "selected-operation",
            "status_code": 201,
            "execution_mode": "mock",
        }
        assert PRIVATE not in output.model_dump_json()
        live.execute.assert_not_called()
        assert runtime.plans == {} and runtime.placements == []
        assert backend.projects.list_projects(data[1]) == ()
    finally:
        reopened.close()


def test_keyset_cursor_does_not_backfill_new_keys_before_it(enabled):
    client, _, _, _, _ = enabled
    for key in [("hr", "v1"), ("hr", "v3"), ("zeta", "v1")]:
        write(client, *key)
    first = page(client, limit=1)
    assert keys(first) == [("hr", "v1")]
    write(client, "alpha", "v1")
    write(client, "hr", "v2")
    remaining = page(client, limit=50, **cursor(first))
    assert keys(remaining) == [("hr", "v2"), ("hr", "v3"), ("zeta", "v1")]
    assert keys(page(client))[0] == ("alpha", "v1")


def test_revocation_and_disabled_host_deny_listing_before_store_access(enabled, monkeypatch):
    client, backend, data, _, _ = enabled
    write(client)
    forbidden = Mock(side_effect=AssertionError("No fixture listing access"))
    monkeypatch.setattr(backend.mock_fixtures, "list_metadata", forbidden)
    with TestClient(
        create_app(replace(backend, mock_fixture_dev_scopes=())), raise_server_exceptions=False
    ) as disabled:
        disabled.headers.update(client.headers)
        assert disabled.get(LIST).status_code == 403
    assert client.get(LIST.replace("/acme/", "/foreign/")).status_code == 401
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    response = client.get(LIST)
    assert response.status_code == 403 and PRIVATE not in response.text
    forbidden.assert_not_called()


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_nondev_listing_is_forbidden_even_with_admin_and_host_profile(enabled, environment):
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
        assert other.get(LIST.replace("/dev/", f"/{environment}/")).status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "after_connection_id=hr",
        "after_connection_revision=v1",
        "limit=1&limit=2",
        "limit=51",
        "limit=0",
        "offset=1",
        "after_connection_id=&after_connection_revision=v1",
    ],
)
def test_invalid_discovery_cursors_are_private_errors(enabled, query):
    client, _, _, _, _ = enabled
    response = client.get(LIST + "?" + query)
    assert response.status_code == 422, response.text
    assert PRIVATE not in response.text


def test_metadata_discovery_does_not_claim_private_content_readiness(enabled):
    client, backend, data, _, database = enabled
    saved = write(client)
    with sqlite3.connect(database) as connection:
        original = connection.execute("SELECT definition_json FROM http_mock_scenarios").fetchone()[
            0
        ]
        corrupt = original.replace("/employees?", "/employeex?", 1)
        assert corrupt != original and len(corrupt.encode()) == len(original.encode())
        connection.execute("UPDATE http_mock_scenarios SET definition_json=?", (corrupt,))
    # Enumeration validates bounded identity/size metadata, not the private digest.
    assert page(client)["items"] == [saved]
    verified = client.get(path())
    assert verified.status_code == 503 and PRIVATE not in verified.text
    actual, live = adapter(backend.mock_fixtures, data[1])
    with pytest.raises(HTTPActivityError) as caught:
        asyncio.run(actual.execute(data[1], "selected-operation", inputs(saved)))
    assert caught.value.code == "unavailable"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    live.execute.assert_not_called()
