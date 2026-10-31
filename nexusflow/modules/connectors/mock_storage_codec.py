"""Bounded canonical encoding for private durable HTTP mock fixtures."""

import base64
import json
from typing import Any

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_scenarios import _definition, checked_scenario
from nexusflow.modules.connectors.mock_storage_types import HTTPMockStorageError
from nexusflow.modules.connectors.mock_types import HTTPMockScenario
from nexusflow.modules.connectors.transport_types import HTTPAttemptRequest
from nexusflow.modules.project_versioning.canonical import canonical

MAX_DOCUMENT_BYTES = 12 * 1024 * 1024
_MAX_BODY = 4 * 1024 * 1024
_FIELDS = {
    "schema_version",
    "kind",
    "scope",
    "connection_id",
    "connection_revision",
    "expected_path",
    "expected_method",
    "expected_content_type",
    "expected_body_base64",
    "status_code",
    "headers",
    "response_body_base64",
}


def _snapshot(value: HTTPMockScenario) -> HTTPMockScenario:
    return checked_scenario(
        value,
        max_request_bytes=_MAX_BODY,
        max_response_bytes=_MAX_BODY,
        max_header_bytes=65536,
    )


def _bounded(encoded: str) -> str:
    if (
        type(encoded) is not str
        or len(encoded) > MAX_DOCUMENT_BYTES
        or len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES
    ):
        raise ValueError
    return encoded


def encode_scenario(value: HTTPMockScenario) -> str:
    """Snapshot a strict fixture, retaining the existing versioned digest encoding."""
    try:
        return _bounded(canonical(_definition(_snapshot(value))))
    except Exception:
        raise HTTPMockStorageError("invalid_request") from None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError


def _body(value: Any) -> bytes:
    if type(value) is not str or len(value) > 4 * ((_MAX_BODY + 2) // 3):
        raise ValueError
    body = base64.b64decode(value, validate=True)
    if len(body) > _MAX_BODY or base64.b64encode(body).decode("ascii") != value:
        raise ValueError
    return body


def decode_scenario(encoded: str) -> HTTPMockScenario:
    """Reject noncanonical, malformed or oversized stored data without private diagnostics."""
    try:
        value = json.loads(
            _bounded(encoded), object_pairs_hook=_unique, parse_constant=_reject_constant
        )
        if (
            type(value) is not dict
            or value.keys() != _FIELDS
            or value["schema_version"] != "0.1"
            or value["kind"] != "http-mock-scenario"
            or type(value["scope"]) is not dict
            or value["scope"].keys() != {"organization_id", "environment_id"}
            or type(value["headers"]) is not list
            or len(value["headers"]) > 100
            or any(type(pair) is not list or len(pair) != 2 for pair in value["headers"])
        ):
            raise ValueError
        scenario = HTTPMockScenario(
            scope=Scope.model_validate(value["scope"], strict=True),
            connection_id=value["connection_id"],
            connection_revision=value["connection_revision"],
            expected_path=value["expected_path"],
            expected_request=HTTPAttemptRequest(
                method=value["expected_method"],
                body=_body(value["expected_body_base64"]),
                content_type=value["expected_content_type"],
            ),
            status_code=value["status_code"],
            headers=tuple((pair[0], pair[1]) for pair in value["headers"]),
            body=_body(value["response_body_base64"]),
        )
        snapshot = _snapshot(scenario)
        if encode_scenario(snapshot) != encoded:
            raise ValueError
        return snapshot
    except Exception:
        raise HTTPMockStorageError("unavailable") from None
