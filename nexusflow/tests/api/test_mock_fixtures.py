"""Signed Dev fixture administration backed by actual private SQLite storage."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.connectors.mock_repository import SQLiteHTTPMockScenarios
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PATH = BASE + "/connections/http-sales/revisions/v1/mock-fixture"
PRIVATE = "never-show-private-fixture"
BODY = {
    "method": "POST",
    "path": "/employees?fixture=" + PRIVATE,
    "json_body": {"employee": PRIVATE},
    "status_code": 201,
    "response_json_body": {"result": PRIVATE},
}


@pytest.fixture
def connected(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "fixtures.db"
    fixtures = SQLiteHTTPMockScenarios(database)
    backend = replace(backend, mock_fixtures=fixtures, mock_fixture_dev_scopes=(scope,))
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime, database
    fixtures.close()


def assert_private(response):
    assert PRIVATE not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_metadata_only_immutable_writes_and_reads(connected, monkeypatch):
    client, backend, data, runtime, _ = connected
    monkeypatch.setattr(runtime, "dispatcher", Mock(side_effect=AssertionError("Runtime access")))
    monkeypatch.setattr(runtime, "snapshot", Mock(side_effect=AssertionError("Runtime access")))
    first = client.put(PATH, json=BODY)
    assert first.status_code == 201, first.text
    saved = first.json()
    assert set(saved) == {
        "scope",
        "connection_id",
        "connection_revision",
        "fixture_digest",
        "stored_bytes",
    }
    assert saved["scope"] == data[1].model_dump(mode="json")
    assert saved["connection_id"] == "http-sales" and saved["connection_revision"] == "v1"
    assert saved["fixture_digest"].startswith("sha256:") and saved["stored_bytes"] > 0
    for response in (first, client.put(PATH, json=BODY), client.get(PATH)):
        assert response.status_code in {200, 201}, response.text
        assert response.json() == saved
        assert_private(response)
    changed = client.put(PATH, json={**BODY, "status_code": 202})
    assert changed.status_code == 409, changed.text
    assert_private(changed)
    assert client.get(PATH.replace("/revisions/v1/", "/revisions/missing/")).status_code == 404
    assert backend.projects.list_projects(data[1]) == () and runtime.plans == {}


def test_metadata_and_pin_survive_store_reopen(connected):
    client, backend, _, _, database = connected
    saved = client.put(PATH, json=BODY)
    assert saved.status_code == 201, saved.text
    backend.mock_fixtures.close()
    reopened = SQLiteHTTPMockScenarios(database)
    try:
        with TestClient(create_app(replace(backend, mock_fixtures=reopened))) as recovered:
            recovered.headers.update(client.headers)
            assert recovered.get(PATH).json() == saved.json()
            assert recovered.put(PATH, json=BODY).json() == saved.json()
    finally:
        reopened.close()


def test_fresh_revocation_blocks_reads_and_exact_retries(connected):
    client, _, data, _, _ = connected
    assert client.put(PATH, json=BODY).status_code == 201
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    for response in (client.get(PATH), client.put(PATH, json=BODY)):
        assert response.status_code == 403, response.text
        assert_private(response)


def test_authorization_precedes_provider_and_profile_inspection(api):  # noqa: F811
    original, backend, data, _ = api
    malformed = replace(backend, mock_fixture_dev_scopes=None)
    with TestClient(create_app(malformed), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        assert client.get(PATH).status_code == 403
        assert client.put(PATH, json=BODY).status_code == 403
    identities, scope, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    for configured, expected in (
        (backend, 403),
        (replace(backend, mock_fixture_dev_scopes=(scope,)), 503),
        (malformed, 503),
    ):
        with TestClient(create_app(configured), raise_server_exceptions=False) as client:
            client.headers.update(original.headers)
            assert client.get(PATH).status_code == expected
            assert client.put(PATH, json=BODY).status_code == expected


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_admin_is_denied_even_with_provider(connected, environment):
    client, _, data, _, _ = connected
    identities, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    identities.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    path = PATH.replace("/dev/", f"/{environment}/")
    for response in (client.get(path), client.put(path, json=BODY)):
        assert response.status_code == 403, response.text
        assert_private(response)


@pytest.mark.parametrize("query", ["unknown=" + PRIVATE, "limit=1", "x=1&x=2"])
def test_query_parameters_are_rejected(connected, query):
    client, _, _, _, _ = connected
    for response in (client.get(PATH + "?" + query), client.put(PATH + "?" + query, json=BODY)):
        assert response.status_code == 422, response.text
        assert_private(response)


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": {"organization_id": "foreign", "environment_id": "prod"}},
        {"fixture_digest": "sha256:" + "0" * 64},
        {"headers": {"Authorization": PRIVATE}},
        {"method": "get"},
        {"path": "https://" + PRIVATE + ".example.com/"},
        {"json_body": [PRIVATE]},
        {"response_json_body": [PRIVATE]},
        {"status_code": True},
        {"status_code": "201"},
        {"status_code": 199},
        {"method": "GET"},
        {"method": "HEAD", "json_body": None},
        {"status_code": 204},
        {"status_code": 304},
    ],
)
def test_invalid_payload_never_echoes_fixture_data(connected, changes):
    client, _, _, _, _ = connected
    response = client.put(PATH, json={**BODY, **changes})
    assert response.status_code == 422, response.text
    assert_private(response)
    assert client.get(PATH).status_code == 404


def test_json_depth_nodes_and_bytes_are_bounded(connected):
    client, _, _, _, _ = connected
    nested = {}
    for _ in range(34):
        nested = {"child": nested}
    for body in (nested, {"items": [None] * 10001}, {"large": "x" * 1048576}):
        response = client.put(PATH, json={**BODY, "response_json_body": body})
        assert response.status_code == 422, response.text
        assert_private(response)
    assert client.get(PATH).status_code == 404


def test_corruption_and_provider_failures_are_private(connected, monkeypatch):
    client, backend, _, _, _ = connected
    assert client.put(PATH, json=BODY).status_code == 201
    backend.mock_fixtures._db.execute("UPDATE http_mock_scenarios SET fixture_digest=?", (PRIVATE,))
    backend.mock_fixtures._db.commit()
    for response in (client.get(PATH), client.put(PATH, json=BODY)):
        assert response.status_code == 503, response.text
        assert_private(response)
    monkeypatch.setattr(backend.mock_fixtures, "put", Mock(side_effect=OSError(PRIVATE)))
    response = client.put(PATH.replace("/revisions/v1/", "/revisions/v2/"), json=BODY)
    assert response.status_code == 503, response.text
    assert_private(response)


def test_quota_conflict_allows_exact_retry(connected):
    client, backend, _, _, _ = connected
    backend.mock_fixtures.max_scenarios_per_scope = 1
    first = client.put(PATH, json=BODY)
    assert first.status_code == 201, first.text
    assert (
        client.put(PATH.replace("/revisions/v1/", "/revisions/v2/"), json=BODY).status_code == 409
    )
    assert client.put(PATH, json=BODY).json() == first.json()


def test_bad_token_and_foreign_scope_are_denied(connected):
    client, _, _, _, _ = connected
    assert client.get(PATH.replace("/acme/", "/foreign/")).status_code == 401
    client.headers["authorization"] = "Bearer invalid-private-token"
    for response in (client.get(PATH), client.put(PATH, json=BODY)):
        assert response.status_code == 401, response.text
        assert_private(response)
