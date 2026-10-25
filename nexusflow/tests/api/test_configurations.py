"""Signed HTTP administration of immutable, metadata-only destination configuration."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.package_promotion.configurations import SQLiteConfigurationRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PATH = BASE + "/projects/hr/configurations"


@pytest.fixture
def configured(api, tmp_path):  # noqa: F811
    original, backend, data, runtime = api
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    configurations = SQLiteConfigurationRepository(tmp_path / "configurations.db")
    backend = replace(backend, configurations=configurations)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, backend, data, runtime
    configurations.close()


def test_put_get_and_retry_expose_metadata_only_before_project_exists(configured):
    client, backend, data, _ = configured
    payload = {"values": {"region": "private-destination-value", "nested": {"enabled": True}}}
    first = client.put(PATH + "/cfg-1", json=payload)
    assert first.status_code == 201, first.text
    metadata = first.json()
    assert metadata["configuration_revision"] == "cfg-1"
    assert metadata["project_id"] == "hr" and metadata["revision"] == 1
    assert metadata["actor_id"] == "pairwise-subject"
    assert "values" not in metadata
    assert "private-destination-value" not in first.text
    assert client.put(PATH + "/cfg-1", json=payload).json() == metadata
    assert client.get(PATH + "/cfg-1").json() == metadata
    assert backend.projects.list_projects(data[1]) == ()
    different = client.put(PATH + "/cfg-1", json={"values": {"region": "different-private"}})
    assert different.status_code == 409
    assert "different-private" not in different.text
    assert client.get(PATH + "/absent").status_code == 404


def test_configuration_history_is_project_scoped_and_cursor_paginated(configured):
    client, _, _, _ = configured
    receipts = []
    for revision in range(1, 4):
        response = client.put(PATH + f"/cfg-{revision}", json={"values": {"secretless": revision}})
        assert response.status_code == 201, response.text
        receipts.append(response.json())
    page = client.get(PATH + "?limit=2").json()
    assert page["items"] == [receipts[2], receipts[1]]
    next_page = client.get(PATH + f"?before_revision={page['next_before_revision']}").json()
    assert next_page["items"] == [receipts[0]]
    assert next_page["next_before_revision"] is None
    assert client.get(PATH.replace("/hr/", "/other/")).json()["items"] == []
    for query in (
        "limit=0",
        "limit=101",
        "before_revision=0",
        "before_revision=9223372036854775808",
    ):
        assert client.get(PATH + "?" + query).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"values": []},
        {"values": {}, "actor_id": "spoof-private"},
        {"values": {}, "scope": {"organization_id": "other"}},
    ],
)
def test_configuration_write_body_is_strict_and_redacted(configured, payload):
    client, _, _, _ = configured
    response = client.put(PATH + "/cfg-1", json=payload)
    assert response.status_code == 422
    assert "spoof-private" not in response.text and "other" not in response.text
    assert client.get(PATH).json()["items"] == []


def test_each_operation_reauthorizes_after_grant_revocation(configured):
    client, _, data, _ = configured
    assert client.put(PATH + "/cfg-1", json={"values": {}}).status_code == 201
    repository, scope, grant, _, _ = data
    repository.revoke_grant(scope, grant.grant_id, expected_revision=2)
    assert client.get(PATH).status_code == 403
    assert client.get(PATH + "/cfg-1").status_code == 403
    assert client.put(PATH + "/cfg-1", json={"values": {}}).status_code == 403


def test_production_configuration_requires_destination_admin_grant(configured):
    client, _, data, _ = configured
    repository, scope, grant, _, _ = data
    prod_scope = scope.model_copy(update={"environment_id": "prod"})
    prod = PATH.replace("/dev/", "/prod/")
    assert client.put(prod + "/cfg-1", json={"values": {}}).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod_scope, role="production_support"), expected_revision=0
    )
    assert client.put(prod + "/cfg-1", json={"values": {}}).status_code == 403
    repository.save_grant(
        replace(grant, scope=prod_scope, role="organization_administrator"), expected_revision=1
    )
    response = client.put(prod + "/cfg-1", json={"values": {}})
    assert response.status_code == 201, response.text
    assert client.get(PATH).json()["items"] == []
    assert client.get(prod + "/cfg-1").json() == response.json()


def test_unconfigured_provider_is_checked_after_authorization(api):  # noqa: F811
    client, _, data, _ = api
    assert client.put(PATH + "/cfg-1", json={"values": {}}).status_code == 403
    assert client.get(PATH).status_code == 403
    assert client.get(PATH + "/cfg-1").status_code == 403
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    assert client.put(PATH + "/cfg-1", json={"values": {}}).status_code == 503
    assert client.get(PATH).status_code == 503
    assert client.get(PATH + "/cfg-1").status_code == 503


def test_configuration_provider_errors_do_not_echo_values(configured, monkeypatch):
    client, backend, _, _ = configured
    for method in ("get", "list", "put"):
        monkeypatch.setattr(
            backend.configurations, method, Mock(side_effect=OSError("private-provider"))
        )
    responses = [
        client.get(PATH),
        client.get(PATH + "/cfg-1"),
        client.put(PATH + "/cfg-1", json={"values": {"private-input": 1}}),
    ]
    for response in responses:
        assert response.status_code == 503, response.text
        assert "private-provider" not in response.text and "private-input" not in response.text
