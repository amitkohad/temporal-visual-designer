"""Wire-level MIME behavior is checked independently with the standard parser."""

import hashlib
import smtplib
import socket
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.email_mime import encode_email_message
from nexusflow.modules.connectors.email_mime_types import EmailEncodingError
from nexusflow.modules.connectors.email_preflight_types import PreparedEmailEnvelope

TIME = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def envelope(**changes):
    value = PreparedEmailEnvelope(
        scope=Scope(organization_id="acme", environment_id="dev"),
        operation_id="operation-1",
        connection_id="hr-mail",
        connection_revision="r1",
        definition_digest="sha256:" + "a" * 64,
        execution_mode="live",
        smtp_host="smtp.private.test",
        port=465,
        sender="sender@example.test",
        to=("employee@example.test",),
        cc=("manager@example.test",),
        bcc=("private-audit@example.test",),
        subject="Welcome aboard",
        text_body="Welcome\nYour first day.",
        secret_reference="private-credential-reference",
    )
    return replace(value, **changes)


def encode(value=None, **options):
    return encode_email_message(
        envelope() if value is None else value, message_time=TIME, **options
    )


def parsed(result):
    value = BytesParser(policy=policy.default).parsebytes(result.data)
    assert value.defects == []
    assert all(header.defects == () for header in value.values() if hasattr(header, "defects"))
    return value


def test_headers_envelope_and_private_configuration():
    source = envelope()
    result = encode(source)
    message = parsed(result)
    assert message["From"].addresses[0].addr_spec == source.sender
    assert message["To"].addresses[0].addr_spec == source.to[0]
    assert message["Cc"].addresses[0].addr_spec == source.cc[0]
    assert message["Bcc"] is None
    assert source.bcc[0].encode() not in result.data
    assert result.envelope_sender == source.sender
    assert result.recipients == source.to + source.cc + source.bcc
    assert message["Message-ID"] == result.message_id
    assert message["Date"].datetime == TIME
    assert str(message["Subject"]) == source.subject
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert message["Content-Transfer-Encoding"] == "base64"
    assert message.get_content() == "Welcome\r\nYour first day.\r\n"
    assert result.scope == source.scope and result.scope is not source.scope
    assert result.operation_id == source.operation_id
    assert result.connection_id == source.connection_id
    assert result.connection_revision == source.connection_revision
    assert result.definition_digest == source.definition_digest
    assert result.message_time == TIME
    assert result.content_digest == "sha256:" + hashlib.sha256(result.data).hexdigest()
    for private in (source.smtp_host, source.secret_reference, source.bcc[0]):
        assert private.encode() not in result.data
    for private in (source.sender, source.to[0], source.subject, source.text_body):
        assert private not in repr(result)


@pytest.mark.parametrize("body", ["a", "a\n", "a\r", "a\r\nb\rc\n", "a\n\n", ".\r\n."])
def test_body_line_endings_and_trailing_newlines(body):
    expected = body.replace("\r\n", "\n").replace("\r", "\n")
    if not expected.endswith("\n"):
        expected += "\n"
    assert parsed(encode(envelope(text_body=body))).get_content() == expected.replace("\n", "\r\n")


def test_unicode_roundtrip_uses_only_ascii_wire():
    subject = "入社へようこそ — Olá 😊 " * 8
    body = "日本語 😀 café\r\nمرحبا\rδοκιμή\n" * 100
    result = encode(envelope(subject=subject, text_body=body))
    message = parsed(result)
    assert str(message["Subject"]) == subject
    assert message.get_content() == body.replace("\r\n", "\n").replace("\r", "\n").replace(
        "\n", "\r\n"
    )
    assert result.data.isascii()
    assert b"\n" not in result.data.replace(b"\r\n", b"")
    assert b"\r" not in result.data.replace(b"\r\n", b"")
    assert result.data.endswith(b"\r\n")
    assert max(map(len, result.data.split(b"\r\n"))) <= 998


@pytest.mark.parametrize(
    "subject",
    [
        "x" * 256,
        " hello  there ",
        "hello " * 40,
        "a  b " * 40,
        "😀" * 256,
        "a" * 60 + " é " + "b" * 60,
    ],
    ids=[
        "long-ascii-token",
        "outer-spaces",
        "many-words",
        "repeated-spaces",
        "max-utf8",
        "mixed-fold",
    ],
)
def test_encoded_subject_preserves_whitespace_and_folding_boundaries(subject):
    result = encode(envelope(subject=subject))
    assert str(parsed(result)["Subject"]) == subject
    assert max(map(len, result.data.split(b"\r\n"))) <= 998


@pytest.mark.parametrize(
    "subject",
    [
        "a\u2028b",
        "a\u2029b",
        "a\u200bb",
        ("😊" * 10 + "\u2028\u2029 ") * 15,
        "=?utf-8?b?QQ==?= literal",
    ],
    ids=[
        "line-separator",
        "paragraph-separator",
        "zero-width-space",
        "separator-chunk-boundaries",
        "encoded-word-text",
    ],
)
def test_subject_preserves_unicode_separators_without_header_reinterpretation(subject):
    result = encode(envelope(subject=subject))
    message = parsed(result)
    assert str(message["Subject"]) == subject
    subject_lines = result.data.split(b"Subject: ", 1)[1].split(b"\r\nDate:", 1)[0].split(b"\r\n")
    assert len(subject_lines[0]) + len(b"Subject: ") <= 76
    assert all(len(line) <= 76 for line in subject_lines[1:])


def test_bcc_content_is_not_redacted_and_optional_cc_omitted():
    source = envelope(cc=(), text_body="Contact private-audit@example.test for assistance")
    message = parsed(encode(source))
    assert message["Cc"] is None and message["Bcc"] is None
    assert source.bcc[0] in message.get_content()


def test_encoding_is_deterministic_and_context_identity_does_not_bind_payload():
    source = envelope()
    first, second = encode(source), encode(source)
    assert first == second
    changed = encode(replace(source, text_body="Changed payload", bcc=("other@example.test",)))
    assert changed.message_id == first.message_id
    assert changed.data != first.data and changed.content_digest != first.content_digest
    later = encode_email_message(source, message_time=TIME.replace(second=1))
    assert later.message_id == first.message_id and later.data != first.data
    assert encode(replace(source, operation_id="operation-2")).message_id != first.message_id
    assert first.data.endswith(b"\r\n") and not first.data.endswith(b"\r\n.\r\n")


def test_maximum_recipient_count_and_long_addresses_roundtrip():
    recipients = tuple(f"person-{index}@example.test" for index in range(50))
    result = encode(envelope(to=recipients[:20], cc=recipients[20:40], bcc=recipients[40:]))
    message = parsed(result)
    assert result.recipients == recipients
    assert len(message["To"].addresses) == 20 and len(message["Cc"].addresses) == 20
    assert max(map(len, result.data.split(b"\r\n"))) <= 998


def test_maximum_body_fits_default_budget():
    body = "a" * 1_048_576
    result = encode(envelope(text_body=body))
    assert parsed(result).get_content() == body + "\r\n"
    assert len(result.data) < 2_097_152


def test_byte_limit_is_inclusive_and_size_errors_are_private():
    result = encode()
    assert encode(max_message_bytes=len(result.data)) == result
    with pytest.raises(EmailEncodingError) as caught:
        encode(max_message_bytes=len(result.data) - 1)
    assert caught.value.code == "message_too_large"
    assert caught.value.retryable is False
    assert caught.value.delivery_may_have_occurred is False
    assert caught.value.__suppress_context__
    assert "private" not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "1000", None, 8_388_609])
def test_invalid_limits_are_closed(limit):
    with pytest.raises(EmailEncodingError) as caught:
        encode(max_message_bytes=limit)
    assert caught.value.code == "invalid_request"


@pytest.mark.parametrize(
    "changes",
    [
        {"subject": "hello\r\nBcc: intruder@example.test"},
        {"sender": "sender@example.test\nX-Injected: true"},
        {"to": ("recipient@example.test\r\nCc: intruder@example.test",)},
        {"execution_mode": "mock"},
        {"text_body": "private\x00secret"},
    ],
)
def test_tampered_input_never_reaches_serializer(monkeypatch, changes):
    forbidden = Mock(side_effect=AssertionError("serializer must not run"))
    monkeypatch.setattr(EmailMessage, "as_bytes", forbidden)
    with pytest.raises(EmailEncodingError) as caught:
        encode(envelope(**changes))
    assert caught.value.code == "invalid_request"
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "wire", [b"bare\n", b"bare\r", b"no-ending", b"\xff\r\n", b"a" * 999 + b"\r\n"]
)
def test_invalid_serializer_output_is_unavailable(monkeypatch, wire):
    monkeypatch.setattr(EmailMessage, "as_bytes", lambda *a, **kw: wire)
    with pytest.raises(EmailEncodingError) as caught:
        encode()
    assert caught.value.code == "unavailable"


def test_serializer_exception_is_private_and_network_is_never_used(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No transport permitted"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)
    encode()
    forbidden.assert_not_called()
    monkeypatch.setattr(EmailMessage, "as_bytes", Mock(side_effect=RuntimeError("private-secret")))
    with pytest.raises(EmailEncodingError) as caught:
        encode()
    assert caught.value.code == "unavailable"
    assert "private-secret" not in repr(caught.value) + str(caught.value)
    assert caught.value.__suppress_context__


def test_result_is_frozen_and_independent_of_original_scope():
    source = envelope()
    result = encode(source)
    with pytest.raises(FrozenInstanceError):
        result.data = b"changed"
    object.__setattr__(source.scope, "organization_id", "changed")
    assert result.scope.organization_id == "acme"
