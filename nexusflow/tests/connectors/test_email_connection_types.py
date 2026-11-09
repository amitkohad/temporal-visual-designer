"""Offline immutable SMTP TLS metadata does not authorize or open a connection."""

import socket
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionWrite,
    canonical_email_domain,
)


def definition(**changes):
    value = {
        "smtp_host": "MAIL.Example.Test",
        "sender": "HR@Example.Test",
        "allowed_recipient_domains": ["Partner.Test", "EXAMPLE.TEST"],
        "secret_reference": "private-secret-reference",
    }
    value.update(changes)
    return EmailConnectionWrite.model_validate(value)


def test_canonical_snapshot_and_private_representation(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No DNS"))
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    original = ["Partner.Test", "EXAMPLE.TEST"]
    value = definition(allowed_recipient_domains=original)
    original.append("changed.test")
    assert value.smtp_host == "mail.example.test"
    assert value.sender == "HR@example.test"
    assert value.allowed_recipient_domains == ("example.test", "partner.test")
    assert value.transport == "smtp_tls" and value.port == 465
    assert "mail.example.test" not in repr(value)
    assert "HR@" not in repr(value) and "private-secret" not in repr(value)
    assert "partner.test" not in repr(value)
    assert "465" not in repr(value)
    forbidden.assert_not_called()
    with pytest.raises(ValidationError):
        value.port = 587


@pytest.mark.parametrize(
    "value",
    [
        "",
        "localhost",
        "127.0.0.1",
        "[::1]",
        "::1",
        "https://mail.example.test",
        "mail.example.test:465",
        "mail.example.test.",
        "*.example.test",
        "user@mail.example.test",
        " mail.example.test",
        "mail.example.test\n",
        "máil.example.test",
        "-mail.example.test",
        "mail..test",
        "x_foo.example.test",
        "1.2.3.04",
        "a" * 64 + ".test",
        "a." * 126 + "zz",
    ],
)
def test_bad_dns_names(value):
    with pytest.raises(ValueError, match="Invalid email DNS name"):
        canonical_email_domain(value)
    for field in ("smtp_host", "allowed_recipient_domains"):
        with pytest.raises(ValidationError):
            definition(**{field: [value] if field == "allowed_recipient_domains" else value})


@pytest.mark.parametrize("value", [None, True, 465, [], {}, b"mail.example.test"])
def test_dns_native_string_required(value):
    with pytest.raises(ValueError, match="Invalid email DNS name"):
        canonical_email_domain(value)


@pytest.mark.parametrize("port", [0, -1, 65536, True, 465.0, "465", None])
def test_bad_port(port):
    with pytest.raises(ValidationError):
        definition(port=port)


@pytest.mark.parametrize("port", [1, 465, 587, 65535])
def test_explicit_tls_port_metadata_is_not_egress_authorization(port):
    assert definition(port=port).port == port


@pytest.mark.parametrize(
    "sender",
    [
        "HR <hr@example.test>",
        "hr@example.test\r\nBcc: other@test.test",
        "hr@example.test\n",
        "hr@localhost",
        "hr@[127.0.0.1]",
        "unicodeé@example.test",
        None,
    ],
)
def test_sender_subset(sender):
    with pytest.raises(ValidationError):
        definition(sender=sender)


@pytest.mark.parametrize(
    "domains",
    [
        [],
        ["example.test", "EXAMPLE.TEST"],
        ["example.test"] * 51,
        "example.test",
        {"example.test"},
        None,
        [True],
    ],
)
def test_domains_shape_and_canonical_uniqueness(domains):
    with pytest.raises(ValidationError):
        definition(allowed_recipient_domains=domains)


@pytest.mark.parametrize(
    "field,value",
    [
        ("password", "private"),
        ("username", "private"),
        ("headers", {"x": "private"}),
        ("scope", {}),
        ("transport", "smtp"),
        ("connector_type", "http"),
        ("connector_version", "2.0.0"),
        ("secret_reference", "https://secret.example.test"),
    ],
)
def test_no_credential_or_unknown_business_fields(field, value):
    with pytest.raises(ValidationError):
        definition(**{field: value})


def test_json_array_tuple_boundary_and_case_canonicalization():
    value = EmailConnectionWrite.model_validate_json(
        '{"smtp_host":"MAIL.EXAMPLE.TEST","sender":"HR@Example.Test",'
        '"allowed_recipient_domains":["Example.Test"]}'
    )
    assert value == definition(allowed_recipient_domains=("example.test",), secret_reference=None)
