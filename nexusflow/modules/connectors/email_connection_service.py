"""Freshly authorized metadata-only email connection administration."""

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import TypeAdapter

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.connectors.email_connection_types import (
    EmailConnectionMetadata,
    EmailConnectionPage,
    EmailConnectionRepository,
    EmailConnectionWrite,
)
from nexusflow.modules.connectors.types import ConnectionError
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.canonical import canonical, digest
from nexusflow.modules.project_versioning.types import ProjectAuthorization

MAX_METADATA_BYTES = 65536


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


class EmailConnectionService:
    def __init__(self, repository: EmailConnectionRepository, authorization: ProjectAuthorization):
        self.repository = repository
        self.authorization = authorization

    def _authorize(self, scope: Scope) -> str:
        try:
            actor = self.authorization.authorize(
                scope.model_copy(deep=True), "connection.manage", "connections"
            )
            return _identifier(actor)
        except IdentityError:
            raise
        except PermissionError:
            raise ConnectionError("forbidden", "Email connection access denied") from None
        except Exception:
            raise ConnectionError(
                "unavailable", "Email connection authorization unavailable"
            ) from None

    def _context(
        self, scope: Scope, connection_id: str, revision: str | None = None
    ) -> tuple[Scope, str]:
        try:
            selected = _scope(scope)
        except Exception:
            raise ConnectionError("invalid_request", "Invalid email connection scope") from None
        actor = self._authorize(selected)
        try:
            _identifier(connection_id)
            if revision is not None:
                _identifier(revision)
        except Exception:
            raise ConnectionError(
                "invalid_request", "Invalid email connection identifier"
            ) from None
        return selected, actor

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except ConnectionError as error:
            try:
                code = error.code
                selected = (
                    code
                    if type(code) is str
                    and code
                    in {"invalid_request", "not_found", "conflict", "limit_exceeded", "unavailable"}
                    else "unavailable"
                )
            except Exception:
                selected = "unavailable"
            raise ConnectionError(
                selected, "Email connection operation could not be completed"
            ) from None
        except Exception:
            raise ConnectionError(
                "unavailable", "Email connection storage is unavailable"
            ) from None

    @staticmethod
    def _metadata(
        value: EmailConnectionMetadata,
        scope: Scope,
        connection_id: str,
        revision: str | None = None,
    ) -> EmailConnectionMetadata:
        if type(value) is not EmailConnectionMetadata:
            raise ValueError
        result = EmailConnectionMetadata.model_validate(
            {
                "scope": _scope(value.scope),
                "connection_id": value.connection_id,
                "connection_revision": value.connection_revision,
                "revision": value.revision,
                "connector_type": value.connector_type,
                "connector_version": value.connector_version,
                "transport": value.transport,
                "definition_digest": value.definition_digest,
                "has_secret_reference": value.has_secret_reference,
                "actor_id": value.actor_id,
                "created_at": value.created_at,
            },
            strict=True,
        )
        timestamp = datetime.fromisoformat(result.created_at)
        if (
            result.scope != scope
            or result.connection_id != connection_id
            or (revision is not None and result.connection_revision != revision)
            or timestamp.tzinfo is None
            or timestamp.utcoffset() != UTC.utcoffset(timestamp)
            or timestamp.isoformat() != result.created_at
            or len(result.model_dump_json().encode("utf-8")) > MAX_METADATA_BYTES
        ):
            raise ValueError
        return result

    def put(
        self, scope: Scope, connection_id: str, revision: str, request: EmailConnectionWrite
    ) -> EmailConnectionMetadata:
        selected, actor = self._context(scope, connection_id, revision)
        try:
            if type(request) is not EmailConnectionWrite:
                raise ValueError
            snapshot = EmailConnectionWrite.model_validate(
                {
                    "connector_type": request.connector_type,
                    "connector_version": request.connector_version,
                    "transport": request.transport,
                    "smtp_host": request.smtp_host,
                    "port": request.port,
                    "sender": request.sender,
                    "allowed_recipient_domains": request.allowed_recipient_domains,
                    "secret_reference": request.secret_reference,
                },
                strict=True,
            )
            definition = snapshot.model_dump(mode="json")
            if len(canonical(definition).encode("utf-8")) > 16384:
                raise ValueError
            expected_digest = digest(definition)
            expected_secret = snapshot.secret_reference is not None
        except Exception:
            raise ConnectionError(
                "invalid_request", "Invalid email connection definition"
            ) from None

        def operation() -> EmailConnectionMetadata:
            result = self._metadata(
                self.repository.put(
                    selected.model_copy(deep=True), connection_id, revision, snapshot, actor
                ),
                selected,
                connection_id,
                revision,
            )
            if (
                result.definition_digest != expected_digest
                or result.has_secret_reference != expected_secret
            ):
                raise ValueError
            return result

        result = self._safe(operation)
        self._authorize(selected)
        return result

    def get(self, scope: Scope, connection_id: str, revision: str) -> EmailConnectionMetadata:
        selected, _ = self._context(scope, connection_id, revision)
        result = self._safe(
            lambda: self._metadata(
                self.repository.metadata(selected.model_copy(deep=True), connection_id, revision),
                selected,
                connection_id,
                revision,
            )
        )
        self._authorize(selected)
        return result

    def list(
        self,
        scope: Scope,
        connection_id: str,
        *,
        limit: int = 25,
        before_revision: int | None = None,
    ) -> EmailConnectionPage:
        selected, _ = self._context(scope, connection_id)
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or (
                before_revision is not None
                and (type(before_revision) is not int or not 1 <= before_revision < 2**63)
            )
        ):
            raise ConnectionError(
                "invalid_request", "Invalid email connection pagination"
            ) from None

        def operation() -> EmailConnectionPage:
            page = self.repository.list(
                selected.model_copy(deep=True), connection_id, limit, before_revision
            )
            if (
                type(page) is not EmailConnectionPage
                or type(page.items) is not tuple
                or len(page.items) > limit
            ):
                raise ValueError
            items = tuple(self._metadata(item, selected, connection_id) for item in page.items)
            numbers = tuple(item.revision for item in items)
            cursor = page.next_before_revision
            if (
                numbers != tuple(sorted(set(numbers), reverse=True))
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
                raise ValueError
            result = EmailConnectionPage(items=items, next_before_revision=cursor)
            if len(result.model_dump_json().encode("utf-8")) > MAX_METADATA_BYTES:
                raise ValueError
            return result

        result = self._safe(operation)
        self._authorize(selected)
        return result
