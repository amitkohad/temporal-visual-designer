"""Private immutable evidence attached to successful deployment operations."""

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.types import ProjectAuthorization

if TYPE_CHECKING:
    from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository

MAX_ATTACHMENT_BYTES = 2_097_152
MAX_OPERATION_ATTACHMENTS = 10


class AttachmentMetadata(Contract):
    attachment_id: Identifier
    display_name: str
    checksum: Digest
    size: int
    actor_id: str
    created_at: str


@dataclass(frozen=True)
class AttachmentDownload:
    metadata: AttachmentMetadata
    content: bytes


class DeploymentAttachmentService:
    def __init__(
        self, repository: "SQLiteActivationRepository", authorization: ProjectAuthorization
    ) -> None:
        self.repository = repository
        self.authorization = authorization

    @staticmethod
    def _identifiers(*identifiers: str) -> None:
        try:
            for identifier in identifiers:
                TypeAdapter(Identifier).validate_python(identifier, strict=True)
        except ValidationError:
            raise ActivationError("invalid_request", "Invalid deployment identifier") from None

    def upload(
        self,
        scope: Scope,
        project_id: str,
        operation_id: str,
        attachment_id: str,
        content: bytes,
        display_name: str = "Approval evidence",
    ) -> AttachmentMetadata:
        actor = self.authorization.authorize(scope, "package.activate", project_id)
        self._identifiers(project_id, operation_id, attachment_id)
        if (
            type(display_name) is not str
            or not display_name.strip()
            or len(display_name) > 128
            or not display_name.isprintable()
            or type(content) is not bytes
            or not content
        ):
            raise ActivationError("invalid_request", "Invalid deployment attachment")
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise ActivationError("attachment_too_large", "Deployment attachment is too large")
        try:
            return self.repository.put_attachment(
                scope, project_id, operation_id, attachment_id, content, display_name, actor
            )
        except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError):
            raise ActivationError("unavailable", "Deployment attachments are unavailable") from None

    def list(
        self, scope: Scope, project_id: str, operation_id: str
    ) -> tuple[AttachmentMetadata, ...]:
        self.authorization.authorize(scope, "run.read", project_id)
        self._identifiers(project_id, operation_id)
        try:
            return self.repository.list_attachments(scope, project_id, operation_id)
        except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError):
            raise ActivationError("unavailable", "Deployment attachments are unavailable") from None

    def download(
        self, scope: Scope, project_id: str, operation_id: str, attachment_id: str
    ) -> AttachmentDownload:
        self.authorization.authorize(scope, "run.read", project_id)
        self._identifiers(project_id, operation_id, attachment_id)
        try:
            return self.repository.download_attachment(
                scope, project_id, operation_id, attachment_id
            )
        except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError):
            raise ActivationError("unavailable", "Deployment attachments are unavailable") from None
