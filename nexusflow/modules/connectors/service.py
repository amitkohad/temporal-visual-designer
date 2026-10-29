"""Freshly authorized, metadata-only administration of HTTP connection revisions."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.types import (
    ConnectionError,
    ConnectionMetadata,
    ConnectionPage,
    ConnectionRepository,
    HTTPConnectionWrite,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest
from nexusflow.modules.project_versioning.types import ProjectAuthorization

MAX_METADATA_BYTES = 65536


class ConnectionService:
    def __init__(self, repository: ConnectionRepository, authorization: ProjectAuthorization):
        self.repository = repository
        self.authorization = authorization

    def _authorize(self, scope: Scope, connection_id: str, revision: str | None = None) -> str:
        actor = self.authorization.authorize(scope, "connection.manage", "connections")
        try:
            for value in (scope.organization_id, scope.environment_id, connection_id, revision):
                if value is not None:
                    TypeAdapter(Identifier).validate_python(value, strict=True)
        except (ValueError, TypeError):
            raise ConnectionError("invalid_request", "Invalid connection identifier") from None
        return actor

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except PermissionError:
            raise
        except ConnectionError as error:
            code = (
                error.code
                if error.code
                in {"not_found", "conflict", "limit_exceeded", "invalid_request", "unavailable"}
                else "unavailable"
            )
            raise ConnectionError(code, "Connection operation could not be completed") from None
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValueError,
            TypeError,
            AttributeError,
            RecursionError,
            OverflowError,
        ):
            raise ConnectionError("unavailable", "Connection storage is unavailable") from None

    @staticmethod
    def _metadata(
        value: ConnectionMetadata, scope: Scope, connection_id: str, revision: str | None = None
    ) -> ConnectionMetadata:
        if not isinstance(value, ConnectionMetadata) or not isinstance(value.scope, Scope):
            raise ValueError("Invalid connection metadata")
        parsed = ConnectionMetadata.model_validate(
            {
                "scope": {
                    "organization_id": value.scope.organization_id,
                    "environment_id": value.scope.environment_id,
                },
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "revision": value.revision,
                "connector_type": value.connector_type,
                "connector_version": value.connector_version,
                "definition_digest": value.definition_digest,
                "has_secret_reference": value.has_secret_reference,
                "actor_id": value.actor_id,
                "created_at": value.created_at,
            },
            strict=True,
        )
        timestamp = datetime.fromisoformat(parsed.created_at)
        if (
            parsed.scope != scope
            or parsed.connection_id != connection_id
            or (revision is not None and parsed.connection_revision != revision)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() != UTC.utcoffset(timestamp)
            or timestamp.isoformat() != parsed.created_at
            or len(parsed.model_dump_json().encode()) > MAX_METADATA_BYTES
        ):
            raise ValueError("Invalid connection metadata binding")
        return parsed

    def put(
        self, scope: Scope, connection_id: str, revision: str, request: HTTPConnectionWrite
    ) -> ConnectionMetadata:
        actor = self._authorize(scope, connection_id, revision)
        try:
            request = HTTPConnectionWrite.model_validate(
                {
                    "connector_type": request.connector_type,
                    "connector_version": request.connector_version,
                    "base_origin": request.base_origin,
                    "allowed_origins": request.allowed_origins,
                    "secret_reference": request.secret_reference,
                },
                strict=True,
            )
            definition = canonical(request.model_dump(mode="json"))
            if len(definition.encode()) > 16384:
                raise ValueError
        except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
            raise ConnectionError("invalid_request", "Invalid HTTP connection definition") from None

        def operation() -> ConnectionMetadata:
            metadata = self._metadata(
                self.repository.put(scope, connection_id, revision, request, actor),
                scope,
                connection_id,
                revision,
            )
            if metadata.definition_digest != digest(
                request.model_dump(mode="json")
            ) or metadata.has_secret_reference != (request.secret_reference is not None):
                raise ValueError("Connection definition binding is invalid")
            return metadata

        return self._safe(operation)

    def get(self, scope: Scope, connection_id: str, revision: str) -> ConnectionMetadata:
        self._authorize(scope, connection_id, revision)
        return self._safe(
            lambda: self._metadata(
                self.repository.metadata(scope, connection_id, revision),
                scope,
                connection_id,
                revision,
            )
        )

    def list(
        self,
        scope: Scope,
        connection_id: str,
        *,
        limit: int = 25,
        before_revision: int | None = None,
    ) -> ConnectionPage:
        self._authorize(scope, connection_id)
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or (
                before_revision is not None
                and (type(before_revision) is not int or not 1 <= before_revision < 2**63)
            )
        ):
            raise ConnectionError("invalid_request", "Invalid connection page")

        def operation() -> ConnectionPage:
            result = self.repository.list(scope, connection_id, limit, before_revision)
            if not isinstance(result, ConnectionPage) or type(result.items) is not tuple:
                raise ValueError("Invalid connection page")
            items = tuple(self._metadata(item, scope, connection_id) for item in result.items)
            numbers = tuple(item.revision for item in items)
            cursor = result.next_before_revision
            if (
                len(items) > limit
                or numbers != tuple(sorted(set(numbers), reverse=True))
                or len({item.connection_revision for item in items}) != len(items)
                or (
                    before_revision is not None
                    and any(number >= before_revision for number in numbers)
                )
                or (
                    cursor is not None
                    and (
                        type(cursor) is not int
                        or not items
                        or len(items) != limit
                        or cursor != items[-1].revision
                    )
                )
            ):
                raise ValueError("Invalid connection continuation")
            page = ConnectionPage(items=items, next_before_revision=cursor)
            if len(page.model_dump_json().encode()) > MAX_METADATA_BYTES:
                raise ValueError("Connection page exceeds limit")
            return page

        return self._safe(operation)
