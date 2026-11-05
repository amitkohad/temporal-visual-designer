"""Pure private email authoring; no transport or delivery capability."""

import json
import socket
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from nexusflow.modules.connectors.email_types import (
    EMAIL_ADDRESS_PATTERN,
    EmailAuthoringError,
    EmailTextActivityInput,
    EmailTextActivityOutput,
    canonical_email_address,
    prepare_email_text_message,
)

PRIVATE = "private-sensitive-marker"
DIGEST = "sha256:" + "a" * 64


def data(**changes):
    value = {
        "connection_id": "mail",
        "connection_revision": "v1",
        "execution_mode": "live",
        "to": [PRIVATE + "@EXAMPLE.TEST"],
        "subject": PRIVATE,
        "text_body": PRIVATE + "\r\nSecond line\nThird line",
    }
    value.update(changes)
    return value


def model(**changes):
    return EmailTextActivityInput.model_validate(data(**changes), strict=True)


def private_failure(value):
    with pytest.raises(EmailAuthoringError) as caught:
        prepare_email_text_message(value)
    assert caught.value.code == "invalid_request"
    assert str(caught.value) == "Email message validation failed"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("Case@EXAMPLE.TEST", "Case@example.test"),
        ("a.b+c@Sub.EXAMPLE.TEST", "a.b+c@sub.example.test"),
        ("!#$%&'*+-/=?^_`{|}~@example.test", "!#$%&'*+-/=?^_`{|}~@example.test"),
        ("a@x-y.example", "a@x-y.example"),
        ("a" * 64 + "@example.test", "a" * 64 + "@example.test"),
        ("a@" + "b" * 63 + ".test", "a@" + "b" * 63 + ".test"),
    ],
)
def test_supported_ascii_subset_is_canonical_and_preserves_local_spelling(address, expected):
    assert canonical_email_address(address) == expected
    assert canonical_email_address(expected) == expected


@pytest.mark.parametrize(
    "address",
    [
        None,
        1,
        True,
        b"a@example.test",
        "",
        "a",
        "a@example",
        "a@@example.test",
        " a@example.test",
        "a@example.test ",
        "Name <a@example.test>",
        '"quoted"@example.test',
        "a(comment)@example.test",
        ".a@example.test",
        "a.@example.test",
        "a..b@example.test",
        "a@-example.test",
        "a@example-.test",
        "a@example..test",
        "a@under_score.test",
        "a@example.test.",
        "a@[127.0.0.1]",
        "a@exämple.test",
        "ä@example.test",
        "a@example.test\n",
        "a@example.test\r\nBcc: other@example.test",
        "a@exam\x00ple.test",
        "a" * 65 + "@example.test",
        "a@" + "b" * 64 + ".test",
        "a" * 64 + "@" + ".".join(["b" * 63] * 3),
    ],
)
def test_unsupported_address_shapes_fail_without_echo(address):
    with pytest.raises(ValueError) as caught:
        canonical_email_address(address)
    assert str(caught.value) == "Unsupported email address"
    assert caught.value.__suppress_context__


def test_whole_address_byte_limit_is_independent_of_label_and_local_limits():
    domain = ".".join(["a" * 63, "b" * 63, "c" * 61])
    address = "l" * 64 + "@" + domain
    assert len(address) == 254
    assert canonical_email_address(address) == address
    with pytest.raises(ValueError):
        canonical_email_address(address + "d")


def test_exported_pattern_has_strict_end_under_schema_search():
    validator = Draft202012Validator({"type": "string", "pattern": EMAIL_ADDRESS_PATTERN})
    assert validator.is_valid("a@example.test")
    assert not validator.is_valid("a@example.test\n")
    assert not validator.is_valid("prefix <a@example.test>")
    assert validator.is_valid("a" * 64 + "@example.test")
    assert not validator.is_valid("a" * 65 + "@example.test")


def test_modes_and_prepared_snapshot_are_private_and_independent():
    message = model(
        execution_mode="mock",
        fixture_digest=DIGEST,
        cc=["manager@EXAMPLE.TEST"],
        bcc=["audit@example.test"],
    )
    prepared = prepare_email_text_message(message)
    assert prepared.to == (PRIVATE + "@example.test",)
    assert prepared.cc == ("manager@example.test",)
    assert prepared.bcc == ("audit@example.test",)
    assert prepared.execution_mode == "mock" and prepared.fixture_digest == DIGEST
    assert PRIVATE not in repr(message) + repr(prepared)
    message.to.append("changed@example.test")
    message.cc.clear()
    assert prepared.to == (PRIVATE + "@example.test",)
    assert prepared.cc == ("manager@example.test",)
    with pytest.raises(FrozenInstanceError):
        prepared.subject = "changed"


def test_local_case_is_preserved_and_compared_exactly():
    message = model(to=["Case@example.test", "case@example.test"])
    assert prepare_email_text_message(message).to == (
        "Case@example.test",
        "case@example.test",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_mode": "mock"},
        {"execution_mode": "mock", "fixture_digest": None},
        {"fixture_digest": DIGEST},
        {"execution_mode": "automatic"},
        {"execution_mode": "mock", "fixture_digest": "bad"},
        {"to": []},
        {"to": ("a@example.test",)},
        {"cc": "a@example.test"},
        {"bcc": None},
        {"to": ["a@example.test"] * 2},
        {"to": ["a@EXAMPLE.TEST"], "cc": ["a@example.test"]},
        {"to": ["a@example.test"], "bcc": ["a@EXAMPLE.TEST"]},
        {"to": [f"a{index}@example.test" for index in range(51)]},
        {"to": [f"a{index}@example.test" for index in range(50)], "cc": ["extra@example.test"]},
        {"subject": ""},
        {"subject": "   "},
        {"subject": "\t"},
        {"subject": "private\r\nBcc: stolen@example.test"},
        {"subject": "a" * 257},
        {"subject": PRIVATE + "\x7f"},
        {"subject": PRIVATE + "\x85"},
        {"subject": PRIVATE + "\ud800"},
        {"subject": 123},
        {"text_body": ""},
        {"text_body": PRIVATE + "\x00"},
        {"text_body": "é" * 524289},
        {"text_body": "a" * 1048577},
        {"text_body": PRIVATE + "\ud800"},
        {"text_body": [PRIVATE]},
        {"connection_id": "bad/" + PRIVATE},
        {"connection_revision": ""},
    ],
)
def test_ordinary_and_bypassed_model_inputs_enforce_same_policy(changes):
    with pytest.raises(ValidationError):
        model(**changes)
    private_failure(EmailTextActivityInput.model_construct(**data(**changes)))


def test_maximum_recipient_subject_and_body_boundaries_are_supported():
    prepared = prepare_email_text_message(
        model(
            to=[f"user{index}@example.test" for index in range(50)],
            subject="😀" * 256,
            text_body="é" * 524288,
        )
    )
    assert len(prepared.to) == 50
    assert len(prepared.subject.encode()) == 1024
    assert len(prepared.text_body.encode()) == 1048576


@pytest.mark.parametrize(
    "field", ["from", "sender", "smtp_host", "password", "headers", "scope", "operation_id"]
)
def test_business_inputs_cannot_select_host_context_or_transport(field):
    with pytest.raises(ValidationError):
        EmailTextActivityInput.model_validate({**data(), field: PRIVATE})


def test_tampered_models_are_revalidated_and_subclasses_are_not_host_contracts():
    message = model()
    message.to.append("injection@example.test\n")
    private_failure(message)
    object.__setattr__(message, "connection_revision", 1)
    private_failure(message)

    class Derived(EmailTextActivityInput):
        pass

    private_failure(Derived.model_validate(data()))
    private_failure(data())
    private_failure(None)


def test_both_input_schema_and_strict_json_loading_accept_serializable_examples():
    message = EmailTextActivityInput.model_validate_json(json.dumps(data()))
    assert message.to == [PRIVATE + "@example.test"]
    Draft202012Validator.check_schema(EmailTextActivityInput.model_json_schema())
    assert model(to=["a@example.test"]).cc == []
    assert model(to=["a@example.test"]).bcc == []


def test_output_is_submission_metadata_and_rejects_provider_details():
    value = {
        "operation_id": "operation-1",
        "execution_mode": "live",
        "submission_status": "accepted",
    }
    output = EmailTextActivityOutput.model_validate(value)
    assert output.model_dump(mode="json") == value
    for changes in ({"submission_status": "delivered"}, {"provider_response": PRIVATE}):
        with pytest.raises(ValidationError):
            EmailTextActivityOutput.model_validate({**value, **changes})


def test_authoring_and_snapshot_preparation_never_use_network(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No email network provider exists"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert prepare_email_text_message(model()).text_body.startswith(PRIVATE)
    forbidden.assert_not_called()
