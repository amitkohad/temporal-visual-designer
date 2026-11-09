"""Signed registry to actual SQLite preflight; no email delivery acceptance."""

import asyncio
import json
import smtplib
import socket
import sqlite3
import threading
from dataclasses import FrozenInstanceError, replace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nexusflow.apps.api import create_app
from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_connection_repository import SQLiteEmailConnectionRepository
from nexusflow.modules.connectors.email_connection_types import EmailConnectionWrite
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner
from nexusflow.modules.connectors.email_preflight_types import EmailPreflightError
from nexusflow.modules.connectors.email_types import EmailTextActivityInput
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

SCOPE = Scope(organization_id="acme", environment_id="dev")
PATH = BASE + "/email-connections/hr-mail/revisions"
PRIVATE = "never-expose-email-preflight"
DEFINITION = {
    "smtp_host": PRIVATE.upper() + ".Example.TEST",
    "port": 465,
    "sender": "Trusted-Sender@Example.TEST",
    "allowed_recipient_domains": ["Partner.Example.TEST", "EXAMPLE.TEST"],
    "secret_reference": PRIVATE + "-secret",
}


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, resource_id):
        self.calls.append((scope, permission, resource_id))
        if (
            self.revoked
            or scope != SCOPE
            or permission != "worker.execute"
            or resource_id != "hr-mail"
        ):
            raise PermissionError(PRIVATE)
        return "email-worker"


@pytest.fixture(autouse=True)
def forbidden_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("Preflight must not contact a provider"))
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
def seeded(api, tmp_path, monkeypatch):  # noqa: F811
    original, backend, data, runtime = api
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "email-preflight.db"
    repository = SQLiteEmailConnectionRepository(database)
    selected = replace(backend, email_connections=repository)
    monkeypatch.setattr(
        runtime, "dispatcher", AsyncMock(side_effect=AssertionError("Runtime start"))
    )
    monkeypatch.setattr(runtime, "snapshot", AsyncMock(side_effect=AssertionError("Runtime read")))
    with TestClient(create_app(selected), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        response = client.put(PATH + "/v1", json=DEFINITION)
        assert response.status_code == 201, response.text
        metadata = response.json()
        assert PRIVATE not in response.text
        yield client, repository, database, metadata, runtime
    repository.close()
    runtime.dispatcher.assert_not_awaited()
    runtime.snapshot.assert_not_awaited()
    assert runtime.plans == {}


def inputs(**changes):
    value = {
        "connection_id": "hr-mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["Person@EXAMPLE.TEST"],
        "cc": ["Manager@Partner.Example.TEST"],
        "bcc": [PRIVATE + "@example.test"],
        "subject": PRIVATE,
        "text_body": PRIVATE + "\nSynthetic onboarding message.",
    }
    value.update(changes)
    return EmailTextActivityInput.model_validate(value)


def run(repository, worker=None, value=None, *, operation_id="operation-a", scope=SCOPE):
    return asyncio.run(
        EmailPreflightPlanner(repository, worker if worker is not None else Worker()).prepare(
            scope, operation_id, inputs() if value is None else value
        )
    )


def fails(call, code):
    with pytest.raises(EmailPreflightError) as caught:
        call()
    error = caught.value
    assert error.code == code
    assert error.retryable is False and error.delivery_may_have_occurred is False
    assert str(error) == "Email preflight failed"
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


def test_signed_definition_reopens_and_selects_trusted_private_envelope(seeded):
    client, repository, database, metadata, _ = seeded
    assert client.get(PATH + "/v1").json() == metadata
    repository.close()
    reopened = SQLiteEmailConnectionRepository(database)
    try:
        worker = Worker()
        message = inputs()
        prepared = run(reopened, worker, message)
        assert prepared.scope == SCOPE and prepared.operation_id == "operation-a"
        assert prepared.connection_id == "hr-mail" and prepared.connection_revision == "v1"
        assert prepared.definition_digest == metadata["definition_digest"]
        assert prepared.execution_mode == "live"
        assert prepared.smtp_host == PRIVATE + ".example.test" and prepared.port == 465
        assert prepared.sender == "Trusted-Sender@example.test"
        assert prepared.secret_reference == PRIVATE + "-secret"
        assert prepared.to == ("Person@example.test",)
        assert prepared.cc == ("Manager@partner.example.test",)
        assert prepared.bcc == (PRIVATE + "@example.test",)
        assert prepared.subject == message.subject and prepared.text_body == message.text_body
        assert PRIVATE not in repr(prepared)
        assert len(worker.calls) == 2
        assert all(call == (SCOPE, "worker.execute", "hr-mail") for call in worker.calls)
        with pytest.raises(FrozenInstanceError):
            prepared.sender = "different@example.test"
        message.to.append("other@example.test")
        assert prepared.to == ("Person@example.test",)
        assert prepared.scope is not SCOPE
    finally:
        reopened.close()


def test_new_revision_cannot_change_prior_pinned_sender_or_policy(seeded):
    client, repository, _, metadata, _ = seeded
    first = run(repository)
    changed = {
        **DEFINITION,
        "smtp_host": "second.example.test",
        "sender": "Second@example.test",
        "secret_reference": "second-reference",
    }
    response = client.put(PATH + "/v2", json=changed)
    assert response.status_code == 201, response.text
    second = run(repository, value=inputs(connection_revision="v2"), operation_id="operation-b")
    original = run(repository)
    assert original == first
    assert original.definition_digest == metadata["definition_digest"]
    assert second.sender == "Second@example.test" and second.smtp_host == "second.example.test"
    assert second.definition_digest == response.json()["definition_digest"]
    assert second.definition_digest != original.definition_digest
    assert first.secret_reference == PRIVATE + "-secret"
    assert second.secret_reference == "second-reference"


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
@pytest.mark.parametrize("domain", ["sub.example.test", "notexample.test"])
def test_exact_recipient_domain_policy_covers_all_lists(seeded, field, domain):
    _, repository, _, _, _ = seeded
    fails(
        lambda: run(repository, value=inputs(**{field: ["Denied@" + domain]})), "blocked_recipient"
    )


def test_authorized_administrator_is_not_worker_permission(seeded, monkeypatch):
    client, repository, _, _, _ = seeded
    assert client.get(PATH + "/v1").status_code == 200
    worker = Worker()
    worker.revoked = True
    private_read = Mock(
        side_effect=AssertionError("Denied workers cannot read private definitions")
    )
    monkeypatch.setattr(repository, "get", private_read)
    fails(lambda: run(repository, worker), "forbidden")
    private_read.assert_not_called()
    assert len(worker.calls) == 1


def test_revocation_after_actual_offloop_read_blocks_result(seeded, monkeypatch):
    _, repository, _, _, _ = seeded
    worker = Worker()
    original = repository.get
    main_thread = threading.get_ident()
    observed = []

    def read(*args):
        observed.append(threading.get_ident())
        result = original(*args)
        worker.revoked = True
        return result

    monkeypatch.setattr(repository, "get", read)
    fails(lambda: run(repository, worker), "forbidden")
    assert observed and all(thread != main_thread for thread in observed)
    assert len(worker.calls) == 2


def test_missing_and_mock_selection_have_private_nonretryable_errors(seeded, monkeypatch):
    _, repository, _, _, _ = seeded
    fails(lambda: run(repository, value=inputs(connection_revision="missing")), "not_found")
    private_read = Mock(side_effect=AssertionError("Mock authoring is not a live preflight"))
    monkeypatch.setattr(repository, "get", private_read)
    message = inputs(execution_mode="mock", fixture_digest="sha256:" + "a" * 64)
    fails(lambda: run(repository, value=message), "forbidden")
    private_read.assert_not_called()


def test_corrupt_private_definition_is_rejected_without_public_leak(seeded):
    client, repository, database, metadata, _ = seeded
    with sqlite3.connect(database) as writer:
        row = writer.execute("SELECT definition_json FROM email_connections").fetchone()
        document = json.loads(row[0])
        document["sender"] = "Attacker@example.test"
        writer.execute("UPDATE email_connections SET definition_json=?", (json.dumps(document),))
    assert client.get(PATH + "/v1").json() == metadata
    fails(lambda: run(repository), "unavailable")


def test_wrong_scope_provider_record_is_rejected(seeded, monkeypatch):
    _, repository, _, _, _ = seeded
    foreign = SCOPE.model_copy(update={"environment_id": "uat"})
    definition = EmailConnectionWrite.model_validate(DEFINITION)
    repository.put(foreign, "hr-mail", "v1", definition, "other-admin")
    record = repository.get(foreign, "hr-mail", "v1")
    monkeypatch.setattr(repository, "get", Mock(return_value=record))
    fails(lambda: run(repository), "invalid_provider_result")


@pytest.mark.parametrize(
    "field", ["sender", "smtp_host", "secret_reference", "operation_id", "scope"]
)
def test_message_json_cannot_supply_host_authority(seeded, field):
    _, repository, _, _, _ = seeded
    raw = inputs().model_dump(mode="json")
    raw[field] = PRIVATE
    with pytest.raises(ValidationError):
        EmailTextActivityInput.model_validate(raw)
    assert run(repository).sender == "Trusted-Sender@example.test"
