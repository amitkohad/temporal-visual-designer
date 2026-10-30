"""Bounded JSON business inputs do not grant scope, credentials or execution identity."""

import math

import pytest
from pydantic import ValidationError

from nexusflow.modules.connectors.activity_types import (
    HTTPJSONActivityInput,
    HTTPJSONActivityOutput,
)


def inputs(**changes):
    value = {
        "connection_id": "hr",
        "connection_revision": "v1",
        "execution_mode": "live",
        "method": "POST",
        "path": "/employees",
        "json_body": {"name": "private-employee-value"},
    }
    return HTTPJSONActivityInput(**(value | changes))


def test_input_payload_and_path_are_hidden_and_output_is_metadata_only():
    value = inputs(path="/private-route")
    assert "private-employee-value" not in repr(value) and "private-route" not in repr(value)
    result = HTTPJSONActivityOutput(operation_id="run-step", status_code=201, execution_mode="live")
    assert set(result.model_dump()) == {"operation_id", "status_code", "execution_mode"}


@pytest.mark.parametrize(
    "field", ["scope", "operation_id", "headers", "credentials", "secret_reference"]
)
def test_business_inputs_cannot_select_trusted_context_or_credentials(field):
    with pytest.raises(ValidationError):
        inputs(**{field: "private-forbidden-value"})


@pytest.mark.parametrize(
    "body", [[], "text", {"v": math.nan}, {"v": math.inf}, {"v": b"bytes"}, {1: "key"}]
)
def test_invalid_json_values_are_rejected(body):
    with pytest.raises(ValidationError):
        inputs(json_body=body)


def test_json_cycles_and_shared_mutable_aliases_are_rejected():
    cycle = {}
    cycle["self"] = cycle
    for body in (cycle, {"left": cycle, "right": cycle}):
        with pytest.raises(ValidationError):
            inputs(json_body=body)
    shared = {}
    with pytest.raises(ValidationError):
        inputs(json_body={"left": shared, "right": shared})


def test_deep_and_wide_json_is_rejected_before_recursive_validation():
    deep = {}
    for _ in range(34):
        deep = {"child": deep}
    for body in (deep, {"items": list(range(10001))}):
        with pytest.raises(ValidationError):
            inputs(json_body=body)


def test_aggregate_keys_and_utf8_wire_size_are_bounded():
    for body in (
        {"a" * 524289: "a", "b" * 524289: "b"},
        {"message": "\U0001f600" * 262145},
        {"message": "\x00" * 200000},
    ):
        with pytest.raises(ValidationError):
            inputs(json_body=body)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_bodyless_methods_are_enforced(method):
    with pytest.raises(ValidationError):
        inputs(method=method)
    assert inputs(method=method, json_body=None).json_body is None


def test_mock_digest_is_required_only_in_mock_mode():
    with pytest.raises(ValidationError):
        inputs(execution_mode="mock")
    with pytest.raises(ValidationError):
        inputs(fixture_digest="sha256:" + "a" * 64)
    assert (
        inputs(execution_mode="mock", fixture_digest="sha256:" + "a" * 64).execution_mode == "mock"
    )
