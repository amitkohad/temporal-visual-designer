"""Signed SQLite registry through MIME bytes; no provider-delivery acceptance."""

import asyncio
import hashlib
import smtplib
import socket
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_connection_repository import SQLiteEmailConnectionRepository
from nexusflow.modules.connectors.email_mime import encode_email_message
from nexusflow.modules.connectors.email_preflight import EmailPreflightPlanner
from nexusflow.modules.connectors.email_preflight_types import EmailPreflightError
from nexusflow.modules.connectors.email_types import EmailTextActivityInput
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401

SCOPE = Scope(organization_id="acme", environment_id="dev")
PATH = BASE + "/email-connections/hr-mail/revisions"
STAMP = datetime(2026, 11, 9, 8, 30, tzinfo=UTC)
PRIVATE_HOST = "private-mail.example.test"
PRIVATE_SECRET = "private-secret-reference"
BCC = "private-recipient@example.test"
DEFINITION = {
    "smtp_host": PRIVATE_HOST,
    "sender": "Trusted-Sender@Example.TEST",
    "allowed_recipient_domains": ["EXAMPLE.TEST", "partner.example.test"],
    "secret_reference": PRIVATE_SECRET,
}


class Worker:
    def __init__(self):
        self.calls = []
        self.revoked = False

    def authorize(self, scope, permission, resource_id):
        self.calls.append((scope, permission, resource_id))
        if self.revoked or (scope, permission, resource_id) != (
            SCOPE,
            "worker.execute",
            "hr-mail",
        ):
            raise PermissionError(PRIVATE_SECRET)
        return "email-worker"


@pytest.fixture(autouse=True)
def no_provider_effect(monkeypatch):
    denied = Mock(side_effect=AssertionError("MIME preparation must not contact a provider"))
    for module, name in (
        (socket, "getaddrinfo"),
        (socket, "create_connection"),
        (asyncio, "open_connection"),
        (smtplib, "SMTP"),
        (smtplib, "SMTP_SSL"),
    ):
        monkeypatch.setattr(module, name, denied)
    yield denied
    denied.assert_not_called()


@pytest.fixture
def registry(api, tmp_path, monkeypatch):  # noqa: F811
    original, backend, data, runtime = api
    identities, _, grant, _, _ = data
    identities.save_grant(replace(grant, role="organization_administrator"), expected_revision=1)
    database = tmp_path / "email-mime.db"
    repository = SQLiteEmailConnectionRepository(database)
    monkeypatch.setattr(runtime, "dispatcher", AsyncMock(side_effect=AssertionError("Dispatch")))
    monkeypatch.setattr(runtime, "snapshot", AsyncMock(side_effect=AssertionError("Runtime read")))
    with TestClient(
        create_app(replace(backend, email_connections=repository)), raise_server_exceptions=False
    ) as client:
        client.headers.update(original.headers)
        response = client.put(PATH + "/v1", json=DEFINITION)
        assert response.status_code == 201, response.text
        assert PRIVATE_HOST not in response.text and PRIVATE_SECRET not in response.text
        yield client, repository, database, response.json()
    repository.close()
    runtime.dispatcher.assert_not_awaited()
    runtime.snapshot.assert_not_awaited()
    assert runtime.plans == {}


def message(**changes):
    fields = {
        "connection_id": "hr-mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": ["Employee@EXAMPLE.TEST"],
        "cc": ["Manager@Partner.Example.TEST"],
        "bcc": [BCC],
        "subject": "Welcome — onboarding ✓",
        "text_body": "Welcome café 👋\r\nNext step\rFinal line",
    }
    fields.update(changes)
    return EmailTextActivityInput.model_validate(fields)


def prepare(repository, *, value=None, worker=None, operation_id="onboarding-1"):
    return asyncio.run(
        EmailPreflightPlanner(repository, Worker() if worker is None else worker).prepare(
            SCOPE, operation_id, message() if value is None else value
        )
    )


def parse(encoded):
    parsed = BytesParser(policy=policy.default).parsebytes(encoded.data)
    assert parsed.defects == []
    assert all(part.defects == [] for part in parsed.walk())
    assert all(not header.defects for _, header in parsed.items())
    return parsed


def test_signed_pinned_definition_reopens_as_deterministic_private_mime(registry):
    client, repository, database, metadata = registry
    assert client.get(PATH + "/v1").json() == metadata
    reopened = SQLiteEmailConnectionRepository(database)
    try:
        worker = Worker()
        source = message()
        envelope = prepare(reopened, value=source, worker=worker)
        first = encode_email_message(envelope, message_time=STAMP)
        second = encode_email_message(prepare(repository), message_time=STAMP)
        assert first == second
        assert first.data == second.data
        assert first.scope == SCOPE and first.operation_id == "onboarding-1"
        assert first.definition_digest == metadata["definition_digest"]
        assert first.connection_id == "hr-mail" and first.connection_revision == "v1"
        assert first.envelope_sender == "Trusted-Sender@example.test"
        assert first.recipients == (
            "Employee@example.test",
            "Manager@partner.example.test",
            BCC,
        )
        assert first.content_digest == "sha256:" + hashlib.sha256(first.data).hexdigest()
        assert len(worker.calls) == 2
        parsed = parse(first)
        assert str(parsed["Subject"]) == source.subject
        assert parsed["From"].addresses[0].addr_spec == first.envelope_sender
        assert tuple(a.addr_spec for a in parsed["To"].addresses) == envelope.to
        assert tuple(a.addr_spec for a in parsed["Cc"].addresses) == envelope.cc
        assert parsed["Bcc"] is None
        assert str(parsed["Message-ID"]) == first.message_id
        assert parsed["Date"].datetime == STAMP
        assert parsed.get_content_type() == "text/plain"
        assert parsed.get_content_charset() == "utf-8"
        assert parsed["Content-Transfer-Encoding"] == "base64"
        assert parsed.get_content().replace("\r\n", "\n") == (
            "Welcome café 👋\nNext step\nFinal line\n"
        )
        first.data.decode("ascii")
        assert first.data.endswith(b"\r\n")
        assert b"\n" not in first.data.replace(b"\r\n", b"")
        assert b"\r" not in first.data.replace(b"\r\n", b"")
        assert max(map(len, first.data.split(b"\r\n"))) <= 998
        for marker in (PRIVATE_HOST, PRIVATE_SECRET, BCC):
            assert marker.encode() not in first.data
            assert marker not in repr(first)
        source.to.append("later@example.test")
        assert first.recipients == second.recipients
        with pytest.raises(FrozenInstanceError):
            first.data = b"changed"
    finally:
        reopened.close()


def test_bcc_omission_does_not_redact_authored_body(registry):
    _, repository, _, _ = registry
    body = "Mention this contact explicitly: " + BCC
    encoded = encode_email_message(
        prepare(repository, value=message(text_body=body)), message_time=STAMP
    )
    parsed = parse(encoded)
    assert parsed["Bcc"] is None
    assert BCC in encoded.recipients
    assert parsed.get_content().rstrip("\r\n") == body


@pytest.mark.parametrize(
    "subject",
    [
        "入社へようこそ — Olá 😊 " * 8,
        "  Onboarding café ✓ — " * 10 + "  ",
        "Long ASCII subject " * 12 + "   ",
        "Before\u2028line separator\u2029paragraph separator café ✓ " * 4,
        "\u2028Before and after\u2029 ",
    ],
    ids=[
        "mixed-unicode-folding",
        "leading-trailing-whitespace",
        "ascii-folding",
        "unicode-separators-folding",
        "unicode-separators-boundaries",
    ],
)
def test_folded_subject_preserves_every_authored_space(registry, subject):
    _, repository, _, _ = registry
    envelope = prepare(repository, value=message(subject=subject))
    encoded = encode_email_message(envelope, message_time=STAMP)
    parsed = parse(encoded)
    assert str(parsed["Subject"]) == subject
    assert len(parsed.get_all("Subject")) == 1
    assert encoded == encode_email_message(envelope, message_time=STAMP)
    assert max(map(len, encoded.data.split(b"\r\n"))) <= 998


def test_logical_message_id_is_stable_without_claiming_payload_deduplication(registry):
    _, repository, _, _ = registry
    first = encode_email_message(prepare(repository), message_time=STAMP)
    changed = encode_email_message(
        prepare(repository, value=message(subject="Different content", bcc=["other@example.test"])),
        message_time=STAMP + timedelta(seconds=1),
    )
    next_operation = encode_email_message(
        prepare(repository, operation_id="onboarding-2"), message_time=STAMP
    )
    assert changed.message_id == first.message_id
    assert changed.data != first.data and changed.content_digest != first.content_digest
    assert next_operation.message_id != first.message_id


def test_admin_new_revision_does_not_change_prior_project_pin(registry):
    client, repository, _, metadata = registry
    original = encode_email_message(prepare(repository), message_time=STAMP)
    response = client.put(
        PATH + "/v2",
        json={**DEFINITION, "sender": "New-Sender@example.test", "secret_reference": "new-secret"},
    )
    assert response.status_code == 201, response.text
    pinned = encode_email_message(prepare(repository), message_time=STAMP)
    updated = encode_email_message(
        prepare(repository, value=message(connection_revision="v2")), message_time=STAMP
    )
    assert pinned == original
    assert pinned.definition_digest == metadata["definition_digest"]
    assert updated.definition_digest == response.json()["definition_digest"]
    assert updated.message_id != original.message_id
    assert updated.envelope_sender == "New-Sender@example.test"
    assert parse(updated)["From"].addresses[0].addr_spec == "New-Sender@example.test"
    assert b"new-secret" not in updated.data


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_preflight_recipient_policy_blocks_materialization(registry, field):
    _, repository, _, _ = registry
    codec = Mock(wraps=encode_email_message)
    with pytest.raises(EmailPreflightError) as caught:
        codec(
            prepare(repository, value=message(**{field: ["Denied@sub.example.test"]})),
            message_time=STAMP,
        )
    assert caught.value.code == "blocked_recipient"
    assert not caught.value.delivery_may_have_occurred
    codec.assert_not_called()


def test_encoding_is_not_fresh_worker_authorization(registry):
    _, repository, _, _ = registry
    worker = Worker()
    prepared = prepare(repository, worker=worker)
    worker.revoked = True
    encoded = encode_email_message(prepared, message_time=STAMP)
    assert encoded.data
    assert len(worker.calls) == 2
    with pytest.raises(EmailPreflightError) as caught:
        prepare(repository, worker=worker)
    assert caught.value.code == "forbidden"
    assert not caught.value.delivery_may_have_occurred
