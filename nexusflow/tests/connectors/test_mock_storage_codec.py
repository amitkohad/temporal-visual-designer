"""Private persisted fixtures share deterministic identities with static fixtures."""

import json
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_scenarios import scenario_digest
from nexusflow.modules.connectors.mock_storage_codec import (
    MAX_DOCUMENT_BYTES,
    decode_scenario,
    encode_scenario,
)
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.project_versioning.canonical import canonical, digest

PRIVATE = "fixture-secret-sentinel"


def fixture(**changes):
    value = HTTPMockScenario(
        scope=Scope(organization_id="acme", environment_id="dev"),
        connection_id="hr",
        connection_revision="r1",
        expected_path="/employees?value=" + PRIVATE,
        expected_request=HTTPAttemptRequest("POST", b"\x00\xff" + PRIVATE.encode()),
        status_code=201,
        headers=(("x-private", PRIVATE + "é"),),
        body=b"\x00\xff" + PRIVATE.encode(),
    )
    return replace(value, **changes)


def error(call, code):
    with pytest.raises(HTTPMockStorageError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == "HTTP mock fixture storage failed"
    assert PRIVATE not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


def test_round_trip_preserves_binary_unicode_order_and_existing_digest():
    original = fixture(headers=(("x-private", "é"), ("x-private", PRIVATE)))
    encoded = encode_scenario(original)
    result = decode_scenario(encoded)
    assert result == original
    assert result is not original
    assert result.scope is not original.scope
    assert result.expected_request is not original.expected_request
    assert result.headers is not original.headers
    assert scenario_digest(result) == scenario_digest(original) == digest(json.loads(encoded))
    assert encode_scenario(result) == encoded
    assert PRIVATE not in repr(result)


def test_snapshots_are_independent_across_reads():
    encoded = encode_scenario(fixture())
    first = decode_scenario(encoded)
    object.__setattr__(first.scope, "environment_id", "mutated")
    assert decode_scenario(encoded).scope.environment_id == "dev"


@pytest.mark.parametrize(
    "changes",
    [
        {"scope": None},
        {"scope": Scope.model_construct(organization_id="acme", environment_id=1)},
        {"connection_id": "bad/" + PRIVATE},
        {"connection_revision": 1},
        {"expected_path": "//other.example.com/"},
        {"expected_request": HTTPAttemptRequest("GET", b"body")},
        {"expected_request": HTTPAttemptRequest("POST", bytearray(b"bad"))},
        {"expected_request": HTTPAttemptRequest("POST", b"x" * (4194304 + 1))},
        {"status_code": True},
        {"status_code": 600},
        {"headers": [["x-private", PRIVATE]]},
        {"headers": (("X-Private", PRIVATE),)},
        {"headers": (("x-private", PRIVATE + "\n"),)},
        {"headers": (("x-private", "x" * 65536),)},
        {"body": bytearray(b"bad")},
        {"body": b"x" * (4194304 + 1)},
    ],
)
def test_encode_rejects_invalid_inputs_privately(changes):
    error(lambda: encode_scenario(fixture(**changes)), "invalid_request")


@pytest.mark.parametrize("value", [None, {}, "private", 1, True])
def test_encode_requires_exact_scenario(value):
    error(lambda: encode_scenario(value), "invalid_request")


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "0.2"),
        ("kind", "other"),
        ("scope", {"organization_id": "acme", "environment_id": "dev", "extra": True}),
        ("scope", {"organization_id": "acme", "environment_id": 1}),
        ("connection_id", 1),
        ("connection_revision", "bad/revision"),
        ("expected_path", "//other.example.com"),
        ("expected_method", "post"),
        ("expected_content_type", "application/xml"),
        ("expected_body_base64", "**"),
        ("expected_body_base64", "YQ"),
        ("expected_body_base64", "YR=="),
        ("expected_body_base64", "YQ==\n"),
        ("expected_body_base64", ["YQ=="]),
        ("response_body_base64", "YR=="),
        ("status_code", True),
        ("status_code", 201.0),
        ("headers", {"x-private": PRIVATE}),
        ("headers", [["x-private"]]),
        ("headers", [["x-private", PRIVATE, "extra"]]),
        ("headers", [["X-private", PRIVATE]]),
        ("headers", [["x-private", 1]]),
    ],
)
def test_decode_rejects_malformed_fields_privately(field, value):
    document = json.loads(encode_scenario(fixture()))
    document[field] = value
    error(lambda: decode_scenario(canonical(document)), "unavailable")


@pytest.mark.parametrize(
    "encoded",
    [
        None,
        b"{}",
        "",
        "null",
        "[]",
        "{}",
        '{"schema_version":"0.1","schema_version":"0.1"}',
        '{"private":NaN}',
        '{"private":Infinity}',
        '{"private":-Infinity}',
        "\ud800",
        "[" * 2000 + "]" * 2000,
    ],
)
def test_decode_rejects_untrusted_documents(encoded):
    error(lambda: decode_scenario(encoded), "unavailable")


def test_decode_requires_exact_field_set():
    document = json.loads(encode_scenario(fixture()))
    document["private"] = PRIVATE
    error(lambda: decode_scenario(canonical(document)), "unavailable")
    del document["private"]
    del document["expected_path"]
    error(lambda: decode_scenario(canonical(document)), "unavailable")


def test_decode_rejects_noncanonical_whitespace_key_order_and_escapes():
    encoded = encode_scenario(fixture())
    for value in (
        encoded + "\n",
        json.dumps(json.loads(encoded)),
        encoded.replace("é", "\\u00e9"),
        json.dumps(dict(reversed(json.loads(encoded).items())), separators=(",", ":")),
    ):
        error(lambda value=value: decode_scenario(value), "unavailable")


def test_decode_rejects_duplicate_nested_key():
    encoded = encode_scenario(fixture()).replace(
        '"organization_id":"acme"', '"organization_id":"acme","organization_id":"acme"'
    )
    error(lambda: decode_scenario(encoded), "unavailable")


def test_codec_supports_maximum_binary_bodies_and_rejects_larger_document():
    value = fixture(
        expected_request=HTTPAttemptRequest("POST", b"q" * 4194304), body=b"r" * 4194304
    )
    encoded = encode_scenario(value)
    assert len(encoded.encode()) < MAX_DOCUMENT_BYTES
    assert decode_scenario(encoded) == value
    error(lambda: decode_scenario(" " * (MAX_DOCUMENT_BYTES + 1)), "unavailable")


def test_utf8_document_limit_is_enforced_before_parsing():
    error(lambda: decode_scenario("é" * (MAX_DOCUMENT_BYTES // 2 + 1)), "unavailable")
