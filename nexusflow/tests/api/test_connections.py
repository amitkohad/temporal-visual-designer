"""Signed metadata-only HTTP connection administration with actual SQLite."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.connectors.repository import SQLiteConnectionRepository
from nexusflow.modules.connectors.types import ConnectionPage
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PATH = BASE + "/connections/http-sales/revisions"
BODY = {
    "base_origin": "https://Private-API.example.com:443/",
    "allowed_origins": ["https://private-api.example.com"],
    "secret_reference": "opaque-private-reference",
}


@pytest.fixture
def connected(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "connections.db"
    connections = SQLiteConnectionRepository(database)
    backend = replace(backend, connections=connections)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime, database
    connections.close()


def test_immutable_canonical_metadata_and_private_definition_not_read(connected, monkeypatch):
    client, backend, data, runtime, _ = connected
    first = client.put(PATH + "/v1", json=BODY)
    assert first.status_code == 201, first.text
    saved = first.json()
    assert saved["scope"] == data[1].model_dump(mode="json")
    assert saved["revision"] == 1 and saved["has_secret_reference"] is True
    assert saved["actor_id"] == "pairwise-subject"
    canonical = {**BODY, "base_origin": "https://private-api.example.com"}
    assert client.put(PATH + "/v1", json=canonical).json() == saved
    monkeypatch.setattr(
        backend.connections, "get", Mock(side_effect=AssertionError("Private read"))
    )
    monkeypatch.setattr(runtime, "dispatcher", Mock(side_effect=AssertionError("Runtime access")))
    monkeypatch.setattr(runtime, "snapshot", Mock(side_effect=AssertionError("Runtime access")))
    for response in (client.get(PATH + "/v1"), client.get(PATH)):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "opaque-private-reference" not in response.text
        assert "private-api.example.com" not in response.text.lower()
        assert "definition_json" not in response.text
    changed = client.put(PATH + "/v1", json={**BODY, "secret_reference": "different-private"})
    assert changed.status_code == 409, changed.text
    assert "different-private" not in changed.text
    assert backend.projects.list_projects(data[1]) == ()
    assert runtime.plans == {}
    assert client.get(PATH + "/missing").status_code == 404


def test_revision_history_is_scoped_and_exclusive(connected):
    client, _, _, _, _ = connected
    saved = []
    for index in range(3):
        response = client.put(PATH + f"/v{index}", json=BODY)
        assert response.status_code == 201, response.text
        saved.append(response.json())
    first = client.get(PATH + "?limit=2").json()
    assert first["items"] == [saved[2], saved[1]]
    second = client.get(PATH + f"?before_revision={first['next_before_revision']}").json()
    assert second == {"items": [saved[0]], "next_before_revision": None}
    assert client.get(PATH.replace("http-sales", "unknown")).json()["items"] == []


def test_fresh_revocation_blocks_reads_and_exact_retry(connected):
    client, _, data, _, _ = connected
    assert client.put(PATH + "/v1", json=BODY).status_code == 201
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    for response in (
        client.get(PATH),
        client.get(PATH + "/v1"),
        client.put(PATH + "/v1", json=BODY),
    ):
        assert response.status_code == 403, response.text


def test_provider_lookup_follows_authorization(api):  # noqa: F811
    client, _, data, _ = api
    for response in (
        client.get(PATH),
        client.get(PATH + "/v1"),
        client.put(PATH + "/v1", json=BODY),
    ):
        assert response.status_code == 403, response.text
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    for response in (
        client.get(PATH),
        client.get(PATH + "/v1"),
        client.put(PATH + "/v1", json=BODY),
    ):
        assert response.status_code == 503, response.text


def test_prod_support_cannot_administer_connections_but_scoped_admin_can(connected):
    client, _, data, _, _ = connected
    identities, scope, grant, _, _ = data
    prod_scope = scope.model_copy(update={"environment_id": "prod"})
    prod = PATH.replace("/dev/", "/prod/")
    identities.save_grant(
        replace(grant, scope=prod_scope, role="production_support"), expected_revision=0
    )
    assert client.put(prod + "/v1", json=BODY).status_code == 403
    identities.save_grant(
        replace(grant, scope=prod_scope, role="organization_administrator"), expected_revision=1
    )
    response = client.put(prod + "/v1", json=BODY)
    assert response.status_code == 201, response.text
    assert client.get(PATH).json()["items"] == []
    assert client.get(PATH + "/v1").status_code == 404
    assert client.get(prod + "/v1").json() == response.json()
    assert client.get(PATH.replace("acme", "foreign") + "/v1").status_code == 401


@pytest.mark.parametrize(
    "mutation",
    [
        {"password": "never-show-password"},
        {"headers": {"Authorization": "never-show-token"}},
        {"base_origin": "https://user:never-show-credential@api.example.com"},
        {"base_origin": "https://api.example.com?token=never-show-token"},
        {"base_origin": "http://api.example.com"},
        {"base_origin": "https://127.0.0.1"},
        {"base_origin": "https://api.example.com/path"},
        {"connector_type": "email"},
    ],
)
def test_invalid_or_secret_bearing_fields_never_echo(connected, mutation):
    client, _, _, _, _ = connected
    response = client.put(PATH + "/v1", json={**BODY, **mutation})
    assert response.status_code == 422, response.text
    assert "never-show" not in response.text
    assert client.get(PATH).json()["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=51",
        "limit=1.0",
        "limit=+1",
        "limit=1&limit=2",
        "before_revision=0",
        "before_revision=9223372036854775808",
        "unknown=secret",
    ],
)
def test_history_query_is_strict(connected, query):
    client, _, _, _, _ = connected
    assert client.get(PATH + "?" + query).status_code == 422


def test_detail_and_put_do_not_accept_query_fields(connected):
    client, _, _, _, _ = connected
    assert client.get(PATH + "/v1?secret=value").status_code == 422
    assert client.put(PATH + "/v1?secret=value", json=BODY).status_code == 422


def test_metadata_survives_factory_and_database_reopen(connected):
    client, backend, _, _, database = connected
    saved = client.put(PATH + "/v1", json=BODY)
    assert saved.status_code == 201, saved.text
    backend.connections.close()
    reopened = SQLiteConnectionRepository(database)
    try:
        with TestClient(create_app(replace(backend, connections=reopened))) as recovered:
            recovered.headers.update(client.headers)
            assert recovered.get(PATH + "/v1").json() == saved.json()
            assert recovered.put(PATH + "/v1", json=BODY).json() == saved.json()
    finally:
        reopened.close()


def test_provider_failures_and_wrong_scope_are_safe(connected, monkeypatch):
    client, backend, data, _, _ = connected
    saved = client.put(PATH + "/v1", json=BODY)
    assert saved.status_code == 201, saved.text
    metadata = backend.connections.metadata(data[1], "http-sales", "v1")
    foreign = metadata.model_copy(
        update={"scope": data[1].model_copy(update={"organization_id": "foreign"})}
    )
    monkeypatch.setattr(backend.connections, "metadata", Mock(return_value=foreign))
    monkeypatch.setattr(
        backend.connections,
        "list",
        Mock(return_value=ConnectionPage(items=(foreign,), next_before_revision=None)),
    )
    for response in (client.get(PATH), client.get(PATH + "/v1")):
        assert response.status_code == 503, response.text
        assert "foreign" not in response.text
    monkeypatch.setattr(
        backend.connections, "put", Mock(side_effect=OSError("never-show-provider"))
    )
    response = client.put(PATH + "/v2", json=BODY)
    assert response.status_code == 503 and "never-show-provider" not in response.text


def test_quota_conflict_does_not_block_existing_exact_retry(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    connections = SQLiteConnectionRepository(
        tmp_path / "quota.db", maximum_revisions_per_connection=1
    )
    try:
        with TestClient(create_app(replace(backend, connections=connections))) as client:
            client.headers.update(original.headers)
            first = client.put(PATH + "/v1", json=BODY)
            assert first.status_code == 201, first.text
            assert client.put(PATH + "/v2", json=BODY).status_code == 409
            assert client.put(PATH + "/v1", json=BODY).json() == first.json()
    finally:
        connections.close()
