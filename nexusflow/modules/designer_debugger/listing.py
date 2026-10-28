"""Bounded contextual seek cursors; cursors never grant authorization."""

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import TypeAdapter, ValidationError, field_validator

from nexusflow.contracts.models import Contract, Identifier, Scope
from nexusflow.modules.designer_debugger.types import DebugError, DebugSession

MAX_CURSOR_LENGTH = 2048


def validate_created_at(value: str) -> None:
    if type(value) is not str or len(value) > 32 or not value.endswith("+00:00"):
        raise ValueError("Invalid creation timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
        raise ValueError("Invalid creation timestamp")


class _Cursor(Contract):
    version: Literal[1]
    organization_id: Identifier
    environment_id: Identifier
    project_id: Identifier
    actor_id: Identifier
    created_at: str
    session_id: Identifier

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        validate_created_at(value)
        return value


def validate_context(scope: Scope, project_id: str, actor_id: str) -> None:
    for value in (scope.organization_id, scope.environment_id, project_id, actor_id):
        TypeAdapter(Identifier).validate_python(value, strict=True)


def encode_cursor(scope: Scope, project_id: str, actor_id: str, session: DebugSession) -> str:
    payload = _Cursor(
        version=1,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        project_id=project_id,
        actor_id=actor_id,
        created_at=session.created_at,
        session_id=session.session_id,
    )
    encoded = base64.urlsafe_b64encode(payload.model_dump_json().encode()).decode().rstrip("=")
    if len(encoded) > MAX_CURSOR_LENGTH:
        raise ValueError("Cursor exceeds limit")
    return encoded


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cursor field")
        result[key] = value
    return result


def decode_cursor(
    scope: Scope, project_id: str, actor_id: str, cursor: str | None
) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        if (
            type(cursor) is not str
            or not 1 <= len(cursor) <= MAX_CURSOR_LENGTH
            or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None
        ):
            raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != cursor:
            raise ValueError
        value = json.loads(raw, object_pairs_hook=_unique)
        if type(value) is not dict or type(value.get("version")) is not int:
            raise ValueError
        parsed = _Cursor.model_validate(value)
        if (
            parsed.organization_id,
            parsed.environment_id,
            parsed.project_id,
            parsed.actor_id,
        ) != (scope.organization_id, scope.environment_id, project_id, actor_id):
            raise ValueError
        return parsed.created_at, parsed.session_id
    except (ValueError, TypeError, UnicodeError, binascii.Error, ValidationError, RecursionError):
        raise DebugError("invalid_request", "Invalid debug session cursor") from None
