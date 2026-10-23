"""Authorized intake does not publish descriptors or execute source bundles."""

import sqlite3

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import Digest, Scope
from nexusflow.modules.activity_catalog.types import (
    CatalogAuthorization,
    CatalogError,
    ImportedBundle,
    IntakeLimits,
    IntakeRepository,
)
from nexusflow.modules.activity_catalog.validation import validate_bundle


class IntakeService:
    def __init__(
        self,
        repository: IntakeRepository,
        authorization: CatalogAuthorization,
        limits: IntakeLimits | None = None,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.limits = limits if limits is not None else IntakeLimits()

    def import_bundle(self, scope: Scope, archive: bytes) -> ImportedBundle:
        actor = self.authorization.authorize(scope, "activity.import")
        validated = validate_bundle(archive, self.limits)
        try:
            return self.repository.put(scope, validated, actor)
        except (OSError, RuntimeError, sqlite3.Error):
            raise CatalogError("unavailable", "Activity intake storage is unavailable") from None

    def _authorize_read(self, scope: Scope, digest: str) -> None:
        self.authorization.authorize(scope, "activity.import")
        try:
            TypeAdapter(Digest).validate_python(digest)
        except ValidationError:
            raise CatalogError("invalid_input", "Invalid activity digest") from None

    def read_import(self, scope: Scope, digest: str) -> ImportedBundle:
        self._authorize_read(scope, digest)
        try:
            return self.repository.get(scope, digest)
        except (OSError, RuntimeError, sqlite3.Error):
            raise CatalogError("unavailable", "Activity intake storage is unavailable") from None

    def download_import(self, scope: Scope, digest: str) -> bytes:
        self._authorize_read(scope, digest)
        try:
            return self.repository.archive(scope, digest)
        except (OSError, RuntimeError, sqlite3.Error):
            raise CatalogError("unavailable", "Activity intake storage is unavailable") from None
