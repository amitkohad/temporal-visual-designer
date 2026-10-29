"""Pure connection definition checks; origin syntax is not an egress policy."""

import socket

import pytest
from pydantic import ValidationError

from nexusflow.modules.connectors.types import HTTPConnectionWrite


def definition(**changes):
    return HTTPConnectionWrite.model_validate(
        {
            "base_origin": "https://api.example.com",
            "allowed_origins": ["https://api.example.com"],
            "secret_reference": "credential-reference-sentinel",
            **changes,
        }
    )


def test_canonical_origins_are_sorted_and_no_dns_is_performed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Metadata validation must not resolve DNS or send requests")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    request = definition(
        base_origin="HTTPS://API.EXAMPLE.COM:443/",
        allowed_origins=["https://other.example.com:8443/", "https://API.EXAMPLE.COM:443"],
    )
    assert request.base_origin == "https://api.example.com"
    assert request.allowed_origins == (
        "https://api.example.com",
        "https://other.example.com:8443",
    )
    assert request.connector_type == "http" and request.connector_version == "1.0.0"


@pytest.mark.parametrize(
    "origin",
    [
        "http://api.example.com",
        "ftp://api.example.com",
        "//api.example.com",
        "https://user:credential@api.example.com",
        "https://user@api.example.com",
        "https://api.example.com/path",
        "https://api.example.com//",
        "https://api.example.com?token=credential",
        "https://api.example.com?",
        "https://api.example.com#credential",
        "https://api.example.com#",
        "https://api.example.com\\anything",
        " https://api.example.com",
        "https://api.example.com\n",
        "https://api.example.com\x00",
        "https://api.ex ample.com",
        "https://api.éxample.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://8.8.8.8",
        "https://[::1]",
        "https://[2001:4860:4860::8888]",
        "https://api.example.com.",
        "https://api..example.com",
        "https://-api.example.com",
        "https://api-.example.com",
        "https://api_example.com",
        "https://" + "a" * 64 + ".example.com",
        "https://" + ".".join(["a" * 63] * 4),
        "https://api.example.com:",
        "https://api.example.com:0",
        "https://api.example.com:65536",
        "https://api.example.com:-1",
        "https://api.example.com:credential",
        "https://api%2eexample.com",
        "https://",
    ],
    ids=lambda value: value[:60].encode("unicode_escape").decode(),
)
def test_ambiguous_nonorigin_or_credential_bearing_urls_are_rejected(origin):
    with pytest.raises(ValidationError):
        definition(base_origin=origin, allowed_origins=[origin])


@pytest.mark.parametrize(
    "allowed",
    [
        [],
        ["https://other.example.com"],
        ["https://api.example.com", "HTTPS://API.EXAMPLE.COM:443/"],
        [f"https://host-{index}.example.com" for index in range(51)],
        ["https://api.example.com", "http://other.example.com"],
        "https://api.example.com",
        {"https://api.example.com"},
    ],
    ids=[
        "empty",
        "base-not-allowed",
        "canonical-duplicate",
        "too-many",
        "unsafe-member",
        "scalar",
        "set",
    ],
)
def test_allowed_origins_are_explicit_bounded_and_unique(allowed):
    with pytest.raises(ValidationError):
        definition(allowed_origins=allowed)


def test_fifty_origins_and_explicit_null_reference_are_supported():
    origins = ["https://api.example.com"] + [
        f"https://host-{index}.example.com" for index in range(49)
    ]
    request = definition(allowed_origins=origins, secret_reference=None)
    assert len(request.allowed_origins) == 50
    assert request.secret_reference is None


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "never-store-inline"},
        {"headers": {"Authorization": "never-store-inline"}},
        {"secret_value": "never-store-inline"},
        {"connector_type": "email"},
        {"connector_version": "2.0.0"},
        {"secret_reference": "https://vault.example.com/secrets/credential"},
        {"secret_reference": "has spaces"},
        {"secret_reference": "a" * 129},
        {"secret_reference": True},
        {"base_origin": True},
    ],
    ids=[
        "password",
        "headers",
        "secret-value",
        "kind",
        "version",
        "reference-uri",
        "reference-space",
        "reference-size",
        "reference-bool",
        "origin-bool",
    ],
)
def test_fixed_definition_has_no_inline_credentials_or_untyped_configuration(changes):
    with pytest.raises(ValidationError):
        definition(**changes)
