"""MIME policy revalidates private snapshots without I/O or authorization claims."""

import hashlib
import socket
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_mime_policy import prepare_email_mime
from nexusflow.modules.connectors.email_mime_types import EmailEncodingError
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope
from nexusflow.modules.project_versioning.canonical import canonical

TIME = datetime(2026, 11, 9, 12, 30, tzinfo=UTC)
PRIVATE = "private-mime-marker"


def envelope(**changes):
    values = dict(
        scope=Scope(organization_id="acme", environment_id="dev"),
        operation_id="operation-1",
        connection_id="hr-mail",
        connection_revision="r1",
        definition_digest="sha256:" + "a" * 64,
        execution_mode="live",
        smtp_host="smtp." + PRIVATE + ".test",
        port=465,
        sender=PRIVATE + "@sender.test",
        to=(PRIVATE + "@example.test",),
        cc=("manager@example.test",),
        bcc=("audit@example.test",),
        subject=PRIVATE,
        text_body=PRIVATE + "\nBody",
        secret_reference=PRIVATE,
    )
    values.update(changes)
    return PreparedEmailEnvelope(**values)


def failed(value, timestamp=TIME):
    with pytest.raises(EmailEncodingError) as caught:
        prepare_email_mime(value, timestamp)
    error = caught.value
    assert error.code == "invalid_request"
    assert not error.retryable and not error.delivery_may_have_occurred
    assert str(error) == "Email encoding failed"
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


def test_identity_matches_canonical_context_and_has_no_private_payload_or_clock(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No network"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    value = envelope()
    result = prepare_email_mime(value, TIME)
    identity = {
        "kind": "email-message-id",
        "schema_version": "0.1",
        "scope": {"organization_id": "acme", "environment_id": "dev"},
        "operation_id": "operation-1",
        "connection_id": "hr-mail",
        "connection_revision": "r1",
        "definition_digest": "sha256:" + "a" * 64,
    }
    expected = hashlib.sha256(canonical(identity).encode()).hexdigest()
    assert result.message_id == f"<nexusflow-{expected}@nexusflow.invalid>"
    assert result.message_time is TIME
    assert result.envelope == value and result.envelope is not value
    assert result.envelope.scope is not value.scope
    assert result.envelope.to is not value.to
    assert PRIVATE not in repr(result) + repr(result.envelope)
    forbidden.assert_not_called()
    object.__setattr__(value.scope, "organization_id", "changed")
    object.__setattr__(value, "subject", "changed")
    assert result.envelope.scope.organization_id == "acme"
    assert result.envelope.subject == PRIVATE
    with pytest.raises(FrozenInstanceError):
        result.message_id = "changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "Different subject"},
        {"text_body": "Different body"},
        {"to": ("other@example.test",)},
        {"bcc": ("other@example.test",)},
        {"sender": "other@sender.test"},
        {"smtp_host": "other.smtp.test", "port": 2465},
        {"secret_reference": None},
    ],
)
def test_logical_identity_does_not_bind_payload_or_private_configuration(changes):
    assert prepare_email_mime(envelope(**changes), TIME + timedelta(days=1)).message_id == (
        prepare_email_mime(envelope(), TIME).message_id
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": Scope(organization_id="other", environment_id="dev")},
        {"scope": Scope(organization_id="acme", environment_id="uat")},
        {"operation_id": "operation-2"},
        {"connection_id": "other-mail"},
        {"connection_revision": "r2"},
        {"definition_digest": "sha256:" + "b" * 64},
    ],
)
def test_logical_identity_partitions_context(changes):
    assert prepare_email_mime(envelope(**changes), TIME).message_id != (
        prepare_email_mime(envelope(), TIME).message_id
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": {"organization_id": "acme", "environment_id": "dev"}},
        {"scope": Scope.model_construct(organization_id="bad\n", environment_id="dev")},
        {"operation_id": "operation\n"},
        {"operation_id": 1},
        {"connection_id": ""},
        {"connection_revision": "r" * 129},
        {"definition_digest": "sha256:" + "a" * 64 + "\n"},
        {"definition_digest": "sha256:" + "A" * 64},
        {"execution_mode": "mock"},
        {"smtp_host": "SMTP.example.test"},
        {"smtp_host": "127.0.0.1"},
        {"smtp_host": "smtp.example.test\r\n"},
        {"port": True},
        {"port": 0},
        {"port": 65536},
        {"sender": "sender@EXAMPLE.test"},
        {"sender": "Sender <sender@example.test>"},
        {"to": []},
        {"to": ()},
        {"to": ("user@EXAMPLE.test",)},
        {"to": ("bad@example.test\r\nBcc:other@example.test",)},
        {"cc": ["manager@example.test"]},
        {"bcc": ("manager@example.test",)},
        {"to": tuple(f"person{i}@example.test" for i in range(50))},
        {"subject": "private\r\nBcc: injected@example.test"},
        {"subject": " "},
        {"subject": b"subject"},
        {"text_body": "body\x00"},
        {"text_body": b"body"},
        {"secret_reference": "private\n"},
        {"secret_reference": 1},
    ],
    ids=lambda value: next(iter(value)),
)
def test_tampered_or_noncanonical_envelope_is_rejected_privately(changes):
    failed(envelope(**changes))


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "2026-11-09T12:30:00+00:00",
        TIME.replace(tzinfo=None),
        TIME.replace(microsecond=1),
        TIME.replace(year=1899),
        TIME.replace(tzinfo=timezone(timedelta(hours=1))),
        TIME.replace(tzinfo=timezone(timedelta(0), "custom-zero")),
    ],
)
def test_timestamp_requires_explicit_native_whole_second_utc(timestamp):
    failed(envelope(), timestamp)


def test_exact_types_cannot_inject_validation_behavior():
    class EnvelopeSubclass(PreparedEmailEnvelope):
        pass

    class ScopeSubclass(Scope):
        pass

    class DatetimeSubclass(datetime):
        pass

    class StringSubclass(str):
        pass

    failed(EnvelopeSubclass(**envelope().__dict__))
    failed(envelope(scope=ScopeSubclass(organization_id="acme", environment_id="dev")))
    failed(envelope(), DatetimeSubclass(2026, 11, 9, tzinfo=UTC))
    failed(envelope(operation_id=StringSubclass("operation-1")))
    failed(envelope(to=(StringSubclass("user@example.test"),)))
    failed(object())


def test_boundary_times_and_minimal_recipient_lists_are_supported():
    value = envelope(cc=(), bcc=(), secret_reference=None)
    for timestamp in (
        datetime(1900, 1, 1, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
    ):
        assert prepare_email_mime(value, timestamp).message_time == timestamp
    fifty = replace(value, to=tuple(f"user{i}@example.test" for i in range(50)))
    assert len(prepare_email_mime(fifty, TIME).envelope.to) == 50


@pytest.mark.parametrize("code", [PRIVATE, None, 1, True, [PRIVATE]])
def test_error_codes_are_closed_and_never_include_caller_diagnostics(code):
    error = EmailEncodingError(code)
    assert error.code == "unavailable"
    assert PRIVATE not in str(error) + repr(error)
    assert not error.retryable and not error.delivery_may_have_occurred
