"""Pure pinned email policy uses no network, credentials or delivery provider."""

import json
import socket
from dataclasses import FrozenInstanceError, replace
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionRecord,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.email_envelope import prepare_email_envelope
from nexusflow.modules.connectors.email_preflight_types import EmailPreflightError
from nexusflow.modules.connectors.email_types import EmailTextActivityInput
from nexusflow.modules.project_versioning.canonical import canonical, digest

SCOPE = Scope(organization_id="acme", environment_id="dev")
PRIVATE = "never-log-preflight"


def inputs(**changes):
    values = {
        "connection_id": "hr-mail",
        "connection_revision": "r1",
        "execution_mode": "live",
        "to": [PRIVATE + "@EXAMPLE.test"],
        "cc": ["manager@partner.test"],
        "bcc": ["audit@example.test"],
        "subject": PRIVATE,
        "text_body": PRIVATE + "\nBody",
    }
    values.update(changes)
    return EmailTextActivityInput.model_validate(values, strict=True)


def record(**changes):
    definition = EmailConnectionWrite(
        smtp_host="smtp." + PRIVATE + ".test",
        sender=PRIVATE + "@sender.test",
        port=2465,
        allowed_recipient_domains=("example.test", "partner.test"),
        secret_reference=PRIVATE,
        **changes,
    )
    value = definition.model_dump(mode="json")
    metadata = EmailConnectionMetadata(
        scope=SCOPE.model_copy(deep=True),
        connection_id="hr-mail",
        connection_revision="r1",
        revision=1,
        definition_digest=digest(value),
        has_secret_reference=definition.secret_reference is not None,
        actor_id="administrator",
        created_at="2026-11-09T08:00:00+00:00",
    )
    return EmailConnectionRecord(metadata, canonical(value))


def fail(
    *,
    code="invalid_provider_result",
    context=SCOPE,
    operation="operation-1",
    value=None,
    message=None,
):
    with pytest.raises(EmailPreflightError) as caught:
        prepare_email_envelope(
            context,
            operation,
            record() if value is None else value,
            inputs() if message is None else message,
        )
    error = caught.value
    assert error.code == code
    assert error.retryable is False and error.delivery_may_have_occurred is False
    assert str(error) == "Email preflight failed"
    assert PRIVATE not in str(error) + repr(error)
    assert error.__suppress_context__


def test_trusted_sender_and_destination_are_selected_without_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No network or credential port"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    value = record()
    result = prepare_email_envelope(SCOPE, "operation-1", value, inputs())
    assert result.scope == SCOPE and result.scope is not SCOPE
    assert result.operation_id == "operation-1"
    assert result.connection_id == "hr-mail" and result.connection_revision == "r1"
    assert result.definition_digest == value.metadata.definition_digest
    assert result.execution_mode == "live"
    assert result.smtp_host == "smtp." + PRIVATE + ".test" and result.port == 2465
    assert result.sender == PRIVATE + "@sender.test"
    assert result.to == (PRIVATE + "@example.test",)
    assert result.cc == ("manager@partner.test",) and result.bcc == ("audit@example.test",)
    assert result.subject == PRIVATE and result.text_body == PRIVATE + "\nBody"
    assert result.secret_reference == PRIVATE
    assert PRIVATE not in repr(result)
    forbidden.assert_not_called()


def test_snapshot_is_independent_of_message_and_context_mutation():
    context = SCOPE.model_copy(deep=True)
    message = inputs()
    value = record()
    result = prepare_email_envelope(context, "operation-1", value, message)
    message.to[0] = "changed@unapproved.test"
    message.cc.clear()
    object.__setattr__(context, "environment_id", "prod")
    object.__setattr__(value.metadata.scope, "organization_id", "other")
    object.__setattr__(value, "definition_json", "changed")
    assert result.scope == SCOPE
    assert result.to == (PRIVATE + "@example.test",) and result.cc == ("manager@partner.test",)
    with pytest.raises(FrozenInstanceError):
        result.sender = "changed@example.test"


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
@pytest.mark.parametrize(
    "address", ["user@sub.example.test", "user@evilexample.test", "user@example.test.evil"]
)
def test_every_recipient_list_requires_exact_domain(field, address):
    fail(code="blocked_recipient", message=inputs(**{field: [address]}))


def test_canonical_recipient_domain_and_independent_sender_domain():
    result = prepare_email_envelope(
        SCOPE, "operation-1", record(), inputs(to=["User@EXAMPLE.TEST"])
    )
    assert result.to == ("User@example.test",)
    assert result.sender.endswith("@sender.test")


@pytest.mark.parametrize("operation", [None, 1, True, "", "bad/" + PRIVATE, "a" * 129])
def test_invalid_host_operation_is_private(operation):
    fail(code="invalid_request", operation=operation)


@pytest.mark.parametrize(
    "context", [None, {}, Scope.model_construct(organization_id=1, environment_id="dev")]
)
def test_invalid_scope_is_business_context_failure(context):
    fail(code="invalid_request", context=context)


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_mode": "mock", "fixture_digest": "sha256:" + "a" * 64},
    ],
)
def test_mock_authoring_does_not_supply_mock_preflight(changes):
    fail(code="forbidden", value=object(), message=inputs(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "\r\n" + PRIVATE),
        ("text_body", "\x00" + PRIVATE),
        ("text_body", "é" * 524289),
        ("to", ["name@example.test\n"]),
        ("to", []),
        ("connection_id", PRIVATE + "/"),
        ("execution_mode", "other"),
        ("fixture_digest", "sha256:" + "a" * 64),
    ],
    ids=[
        "subject-controls",
        "body-nul",
        "body-bytes",
        "recipient-lf",
        "empty-to",
        "bad-connection",
        "bad-mode",
        "live-pin",
    ],
)
def test_constructed_input_is_revalidated(field, value):
    message = inputs().model_copy(update={field: value})
    fail(code="invalid_request", message=message)


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": Scope(organization_id="other", environment_id="dev")},
        {"scope": Scope(organization_id="acme", environment_id="prod")},
        {"connection_id": "different"},
        {"connection_revision": "r2"},
        {"revision": True},
        {"revision": 0},
        {"connector_type": "http"},
        {"connector_version": "2.0.0"},
        {"transport": "smtp"},
        {"has_secret_reference": False},
        {"has_secret_reference": 1},
        {"definition_digest": "sha256:" + "a" * 64},
        {"definition_digest": "bad-" + PRIVATE},
        {"actor_id": "bad/actor"},
        {"created_at": "2026-11-09T08:00:00"},
        {"created_at": "2026-11-09T08:00:00Z"},
        {"created_at": "2026-11-09T08:00:00+01:00"},
        {"created_at": PRIVATE},
    ],
)
def test_tampered_metadata_cannot_enter_snapshot(changes):
    original = record()
    fail(value=replace(original, metadata=original.metadata.model_copy(update=changes)))


@pytest.mark.parametrize(
    "encoded",
    [
        "[]",
        "null",
        "{}",
        "{",
        PRIVATE,
        " " * 16385,
        '{"port":465,"port":466}',
        '{"port":NaN}',
        '{"port":Infinity}',
    ],
)
def test_malformed_or_oversized_private_definition_fails_closed(encoded):
    fail(value=replace(record(), definition_json=encoded))


@pytest.mark.parametrize(
    "change",
    [
        lambda value: " " + value,
        lambda value: value.replace("smtp.", "SMTP."),
        lambda value: value.replace('"smtp_tls"', '"smtp"'),
        lambda value: value.replace('"port":2465', '"port":true'),
        lambda value: value.replace('"port":2465,', ""),
        lambda value: value.replace('"sender":', '"unknown":'),
    ],
)
def test_noncanonical_or_invalid_definition_cannot_match_digest(change):
    original = record()
    encoded = change(original.definition_json)
    parsed = json.loads(encoded)
    metadata = original.metadata.model_copy(update={"definition_digest": digest(parsed)})
    fail(value=EmailConnectionRecord(metadata, encoded))


def test_no_secret_reference_is_valid_without_resolving_anything():
    original = record()
    value = json.loads(original.definition_json)
    value["secret_reference"] = None
    metadata = original.metadata.model_copy(
        update={
            "definition_digest": digest(value),
            "has_secret_reference": False,
        }
    )
    result = prepare_email_envelope(
        SCOPE, "operation-1", EmailConnectionRecord(metadata, canonical(value)), inputs()
    )
    assert result.secret_reference is None


def test_subclassed_scope_input_and_provider_models_are_rejected():
    class OtherScope(Scope):
        pass

    class OtherInput(EmailTextActivityInput):
        pass

    class OtherMetadata(EmailConnectionMetadata):
        pass

    fail(code="invalid_request", context=OtherScope(organization_id="acme", environment_id="dev"))
    fail(code="invalid_request", message=OtherInput.model_validate(inputs().model_dump()))
    fail(
        value=replace(
            record(), metadata=OtherMetadata.model_validate(record().metadata.model_dump())
        )
    )


def test_unknown_error_code_is_sanitized():
    error = EmailPreflightError(PRIVATE)
    assert error.code == "unavailable" and str(error) == "Email preflight failed"


@pytest.mark.parametrize("value", [None, {}, object(), "not-a-record"])
def test_wrong_provider_record_shape_is_private(value):
    with pytest.raises(EmailPreflightError) as caught:
        prepare_email_envelope(SCOPE, "operation-1", value, inputs())
    assert caught.value.code == "invalid_provider_result"
    assert str(caught.value) == "Email preflight failed" and caught.value.__suppress_context__


@pytest.mark.parametrize("definition", [b"private", "\ud800", 123])
def test_wrong_or_unencodable_definition_shape_is_private(definition):
    fail(value=replace(record(), definition_json=definition))


def test_record_subclass_and_nonnative_metadata_fields_are_rejected():
    class OtherRecord(EmailConnectionRecord):
        pass

    class OtherString(str):
        pass

    original = record()
    fail(value=OtherRecord(original.metadata, original.definition_json))
    fail(
        value=replace(
            original,
            metadata=original.metadata.model_copy(update={"actor_id": OtherString("admin")}),
        )
    )
