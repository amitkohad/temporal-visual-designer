"""Bounded immutable host-provided HTTP mock fixtures; no execution or persistence."""

import base64
import re
from typing import Any

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.attempt_policy import checked_attempt
from nexusflow.modules.connectors.mock_types import HTTPMockError, HTTPMockScenario
from nexusflow.modules.connectors.policy import request_url
from nexusflow.modules.project_versioning.canonical import canonical, digest

_MAX_BODY = 4194304
_MAX_HEADERS = 65536


def _scope(value: Scope) -> Scope:
    if type(value) is not Scope:
        raise ValueError
    return Scope.model_validate(
        {"organization_id": value.organization_id, "environment_id": value.environment_id},
        strict=True,
    )


def _identifier(value: str) -> str:
    if type(value) is not str:
        raise ValueError
    return TypeAdapter(Identifier).validate_python(value, strict=True)


def checked_scenario(
    value: HTTPMockScenario,
    *,
    max_request_bytes: int = 1048576,
    max_response_bytes: int = 1048576,
    max_header_bytes: int = 16384,
) -> HTTPMockScenario:
    """Validate strict private fields and return an independent immutable snapshot."""
    try:
        if (
            any(
                type(limit) is not int or not 0 <= limit <= _MAX_BODY
                for limit in (max_request_bytes, max_response_bytes)
            )
            or type(max_header_bytes) is not int
            or not 1024 <= max_header_bytes <= _MAX_HEADERS
            or type(value) is not HTTPMockScenario
        ):
            raise ValueError
        scope = _scope(value.scope)
        connection_id = _identifier(value.connection_id)
        revision = _identifier(value.connection_revision)
        if type(value.expected_path) is not str:
            raise ValueError
        if request_url("https://mock.example.com", value.expected_path) != (
            "https://mock.example.com" + value.expected_path
        ):
            raise ValueError
        request = checked_attempt(value.expected_request, max_request_bytes)
        if (
            type(value.status_code) is not int
            or not 200 <= value.status_code <= 599
            or type(value.body) is not bytes
            or len(value.body) > max_response_bytes
            or ((request.method == "HEAD" or value.status_code in (204, 304)) and value.body)
            or type(value.headers) is not tuple
            or len(value.headers) > 100
        ):
            raise ValueError
        total = 0
        headers: list[tuple[str, str]] = []
        for pair in value.headers:
            if (
                type(pair) is not tuple
                or len(pair) != 2
                or type(pair[0]) is not str
                or type(pair[1]) is not str
                or total + len(pair[0]) + len(pair[1]) + 4 > max_header_bytes
                or re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", pair[0]) is None
                or any(ord(char) < 32 or ord(char) == 127 for char in pair[1])
            ):
                raise ValueError
            total += len(pair[0].encode("ascii")) + len(pair[1].encode("latin-1")) + 4
            if total > max_header_bytes:
                raise ValueError
            headers.append((pair[0], pair[1]))
        return HTTPMockScenario(
            scope=scope,
            connection_id=connection_id,
            connection_revision=revision,
            expected_path=value.expected_path,
            expected_request=request,
            status_code=value.status_code,
            headers=tuple(headers),
            body=value.body,
        )
    except Exception:
        raise HTTPMockError("invalid_provider_result") from None


def _definition(value: HTTPMockScenario) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "kind": "http-mock-scenario",
        "scope": {
            "organization_id": value.scope.organization_id,
            "environment_id": value.scope.environment_id,
        },
        "connection_id": value.connection_id,
        "connection_revision": value.connection_revision,
        "expected_path": value.expected_path,
        "expected_method": value.expected_request.method,
        "expected_content_type": value.expected_request.content_type,
        "expected_body_base64": base64.b64encode(value.expected_request.body).decode("ascii"),
        "status_code": value.status_code,
        "headers": value.headers,
        "response_body_base64": base64.b64encode(value.body).decode("ascii"),
    }


def _maximum_snapshot(value: HTTPMockScenario) -> HTTPMockScenario:
    return checked_scenario(
        value,
        max_request_bytes=_MAX_BODY,
        max_response_bytes=_MAX_BODY,
        max_header_bytes=_MAX_HEADERS,
    )


def scenario_digest(value: HTTPMockScenario) -> str:
    """Versioned deterministic identity; operation IDs are deliberately not fixture data."""
    return digest(_definition(_maximum_snapshot(value)))


class StaticHTTPMockScenarios:
    """Explicit bounded in-memory fixtures, with independent copies on every read."""

    def __init__(
        self,
        scenarios: tuple[HTTPMockScenario, ...],
        *,
        max_scenarios: int = 100,
        max_total_bytes: int = 8388608,
    ) -> None:
        try:
            if (
                type(max_scenarios) is not int
                or not 1 <= max_scenarios <= 1000
                or type(max_total_bytes) is not int
                or not 1 <= max_total_bytes <= 67108864
                or type(scenarios) is not tuple
                or len(scenarios) > max_scenarios
            ):
                raise ValueError
            snapshots: dict[tuple[str, str, str, str], HTTPMockScenario] = {}
            total = 0
            for value in scenarios:
                snapshot = _maximum_snapshot(value)
                key = self._key(
                    snapshot.scope, snapshot.connection_id, snapshot.connection_revision
                )
                if key in snapshots:
                    raise ValueError
                total += len(canonical(_definition(snapshot)).encode("utf-8"))
                if total > max_total_bytes:
                    raise ValueError
                snapshots[key] = snapshot
        except Exception:
            raise HTTPMockError("invalid_provider_result") from None
        self._snapshots = snapshots

    @staticmethod
    def _key(scope: Scope, connection_id: str, revision: str) -> tuple[str, str, str, str]:
        selected = _scope(scope)
        return (
            selected.organization_id,
            selected.environment_id,
            _identifier(connection_id),
            _identifier(revision),
        )

    async def get(
        self, scope: Scope, connection_id: str, connection_revision: str
    ) -> HTTPMockScenario | None:
        try:
            key = self._key(scope, connection_id, connection_revision)
        except Exception:
            raise HTTPMockError("invalid_request") from None
        result = self._snapshots.get(key)
        return None if result is None else _maximum_snapshot(result)
