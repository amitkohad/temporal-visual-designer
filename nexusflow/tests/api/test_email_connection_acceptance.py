"""Signed metadata administration over real SQLite; no email provider acceptance."""

import asyncio
import json
import smtplib
import socket
import sqlite3
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.connectors.email_connection_repository import SQLiteEmailConnectionRepository
from nexusflow.modules.connectors.email_types import (
    EmailTextActivityInput,
    prepare_email_text_message,
)
from nexusflow.modules.connectors.types import ConnectionError
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

PATH = BASE + "/email-connections/hr-mail/revisions"
BODY = {
    "smtp_host": "SMTP.Private.example.test",
    "sender": "Private-Sender@Example.TEST",
    "allowed_recipient_domains": ["Partner.Example.TEST", "Private-Policy.example.test"],
    "secret_reference": "opaque-mail-secret-reference",
}
PRIVATE_VALUES = (
    "smtp.private.example.test",
    "private-sender@example.test",
    "partner.example.test",
    "private-policy.example.test",
    "opaque-mail-secret-reference",
)


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Metadata must not contact a provider"))
    for module, name in (
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
        (asyncio, "open_connection"),
        (smtplib, "SMTP"),
        (smtplib, "SMTP_SSL"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    yield forbidden
    forbidden.assert_not_called()


@pytest.fixture
def connected(api, tmp_path, monkeypatch):  # noqa: F811
    original, backend, data, runtime = api
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "email-connections.db"
    repository = SQLiteEmailConnectionRepository(database)
    monkeypatch.setattr(
        runtime, "dispatcher", AsyncMock(side_effect=AssertionError("Runtime start"))
    )
    monkeypatch.setattr(runtime, "snapshot", AsyncMock(side_effect=AssertionError("Runtime read")))
    selected = replace(backend, email_connections=repository)
    with TestClient(create_app(selected), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, selected, data, runtime, database
    repository.close()
    runtime.dispatcher.assert_not_awaited()
    runtime.snapshot.assert_not_awaited()
    assert runtime.plans == {}


def private_response(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert all(value not in response.text.lower() for value in PRIVATE_VALUES)
    return response.json()


def test_canonical_retry_survives_reopen_and_retains_identity(connected):
    client, backend, data, _, database = connected
    first = private_response(client.put(PATH + "/v1", json=BODY), 201)
    assert first["scope"] == data[1].model_dump(mode="json")
    assert first["connector_type"] == "email" and first["transport"] == "smtp_tls"
    assert first["actor_id"] == "pairwise-subject" and first["has_secret_reference"] is True
    assert not {"smtp_host", "port", "sender", "allowed_recipient_domains", "secret_reference"} & (
        first.keys()
    )
    canonical = {
        **BODY,
        "smtp_host": "smtp.private.example.test",
        "sender": "Private-Sender@example.test",
        "port": 465,
        "allowed_recipient_domains": ["private-policy.example.test", "partner.example.test"],
    }
    assert private_response(client.put(PATH + "/v1", json=canonical), 201) == first
    backend.email_connections.close()
    reopened = SQLiteEmailConnectionRepository(database)
    try:
        with TestClient(create_app(replace(backend, email_connections=reopened))) as recovered:
            recovered.headers.update(client.headers)
            assert private_response(recovered.get(PATH + "/v1"), 200) == first
            assert private_response(recovered.put(PATH + "/v1", json=canonical), 201) == first
            changed = {**BODY, "sender": "Other-Private-Sender@example.test"}
            error = private_response(recovered.put(PATH + "/v1", json=changed), 409)
            assert "other-private" not in json.dumps(error).lower()
        record = reopened.get(data[1], "hr-mail", "v1")
        definition = json.loads(record.definition_json)
        assert definition["sender"] == "Private-Sender@example.test"
        assert definition["smtp_host"] == "smtp.private.example.test"
        assert definition["allowed_recipient_domains"] == [
            "partner.example.test",
            "private-policy.example.test",
        ]
        assert "opaque-mail-secret-reference" not in repr(record)
    finally:
        reopened.close()


def test_history_is_numeric_exclusive_and_scoped(connected):
    client, _, _, _, _ = connected
    saved = []
    for revision in ("v1", "v10", "v2"):
        saved.append(private_response(client.put(PATH + "/" + revision, json=BODY), 201))
    first = private_response(client.get(PATH + "?limit=2"), 200)
    assert first["items"] == [saved[2], saved[1]]
    assert first["next_before_revision"] == 2
    second = private_response(client.get(PATH + "?limit=2&before_revision=2"), 200)
    assert second == {"items": [saved[0]], "next_before_revision": None}
    assert private_response(client.get(PATH.replace("hr-mail", "missing")), 200)["items"] == []
    assert client.get(PATH.replace("acme", "foreign") + "/v1").status_code == 401


def test_metadata_reads_never_use_private_definition_port(connected, monkeypatch):
    client, backend, _, _, _ = connected
    saved = private_response(client.put(PATH + "/v1", json=BODY), 201)
    private_read = Mock(side_effect=AssertionError("Private definition must not be loaded"))
    monkeypatch.setattr(backend.email_connections, "get", private_read)
    assert private_response(client.get(PATH + "/v1"), 200) == saved
    assert private_response(client.get(PATH), 200)["items"] == [saved]
    private_read.assert_not_called()


def test_corrupt_private_definition_does_not_turn_metadata_into_delivery_readiness(connected):
    client, backend, data, _, database = connected
    saved = private_response(client.put(PATH + "/v1", json=BODY), 201)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE email_connections SET definition_json=? WHERE connection_id=?",
            ('{"private":"corrupt-definition"}', "hr-mail"),
        )
    assert private_response(client.get(PATH + "/v1"), 200) == saved
    assert private_response(client.get(PATH), 200)["items"] == [saved]
    with pytest.raises(ConnectionError) as caught:
        backend.email_connections.get(data[1], "hr-mail", "v1")
    assert caught.value.code == "unavailable"
    assert "corrupt-definition" not in str(caught.value)
    private_response(client.put(PATH + "/v1", json=BODY), 503)


def test_fresh_revocation_denies_all_metadata_paths_before_storage(connected, monkeypatch):
    client, backend, data, _, _ = connected
    private_response(client.put(PATH + "/v1", json=BODY), 201)
    identities, scope, grant, _, _ = data
    identities.revoke_grant(scope, grant.grant_id, expected_revision=2)
    blocked = Mock(side_effect=AssertionError("Revoked request reached storage"))
    for name in ("put", "get", "metadata", "list"):
        monkeypatch.setattr(backend.email_connections, name, blocked)
    for response in (
        client.get(PATH),
        client.get(PATH + "/v1"),
        client.put(PATH + "/v1", json=BODY),
    ):
        private_response(response, 403)
    blocked.assert_not_called()


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_scoped_admin_can_configure_targets_without_production_support_authority(
    connected, environment
):
    client, _, data, _, _ = connected
    identities, scope, grant, _, _ = data
    target_scope = scope.model_copy(update={"environment_id": environment})
    target = PATH.replace("/dev/", f"/{environment}/")
    identities.save_grant(
        replace(grant, scope=target_scope, role="production_support"), expected_revision=0
    )
    private_response(client.put(target + "/v1", json=BODY), 403)
    identities.save_grant(
        replace(grant, scope=target_scope, role="organization_administrator"), expected_revision=1
    )
    saved = private_response(client.put(target + "/v1", json=BODY), 201)
    assert saved["scope"] == target_scope.model_dump(mode="json")
    assert private_response(client.get(target + "/v1"), 200) == saved
    private_response(client.get(PATH + "/v1"), 404)
    assert private_response(client.get(PATH), 200)["items"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"password": "do-not-return-password"},
        {"smtp_host": "https://private-user:do-not-return-password@mail.example.test"},
        {"sender": "Private-Sender@example.test\r\nBcc: do-not-return@example.test"},
        {"allowed_recipient_domains": ["*.do-not-return.example.test"]},
        {"scope": {"organization_id": "foreign", "environment_id": "prod"}},
    ],
)
def test_private_or_authority_injection_is_fixed_error_and_stores_nothing(connected, mutation):
    client, _, _, _, _ = connected
    response = client.put(PATH + "/v1", json={**BODY, **mutation})
    private_response(response, 422)
    assert "do-not-return" not in response.text
    assert private_response(client.get(PATH), 200)["items"] == []


def test_prior_email_authoring_contract_can_reference_metadata_without_execution(connected):
    client, _, _, _, _ = connected
    saved = private_response(client.put(PATH + "/v1", json=BODY), 201)
    prepared = prepare_email_text_message(
        EmailTextActivityInput(
            connection_id=saved["connection_id"],
            connection_revision=saved["connection_revision"],
            execution_mode="live",
            to=["employee@partner.example.test"],
            subject="Synthetic onboarding",
            text_body="This is authoring data only.",
        )
    )
    assert prepared.connection_id == "hr-mail" and prepared.connection_revision == "v1"
    assert "employee@" not in repr(prepared)
