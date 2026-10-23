"""Freshly authorized immutable publication and scoped palette operations."""

import sqlite3
from collections.abc import Callable
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import ActivityRef, Digest, Scope
from nexusflow.modules.activity_catalog.catalog_types import (
    CatalogEntry,
    CatalogEvent,
    CatalogRepository,
    ExportedActivity,
    RuntimeCapabilities,
)
from nexusflow.modules.activity_catalog.types import (
    ActivityManifest,
    CatalogAuthorization,
    CatalogError,
    IntakeRepository,
)
from nexusflow.modules.activity_catalog.validation import validate_bundle


def _storage[Result](operation: Callable[[], Result]) -> Result:
    try:
        return operation()
    except (OSError, RuntimeError, sqlite3.Error):
        raise CatalogError("unavailable", "Activity catalog storage is unavailable") from None


class CatalogService:
    def __init__(
        self,
        repository: CatalogRepository,
        intake: IntakeRepository,
        authorization: CatalogAuthorization,
        capabilities: RuntimeCapabilities,
    ) -> None:
        self.repository = repository
        self.intake = intake
        self.authorization = authorization
        self.capabilities = capabilities

    def publish(self, scope: Scope, digest: str) -> CatalogEntry:
        actor = self.authorization.authorize(scope, "activity.publish")
        try:
            TypeAdapter(Digest).validate_python(digest)
        except ValidationError:
            raise CatalogError("invalid_input", "Invalid activity digest") from None
        existing = _storage(lambda: self.repository.by_digest(scope, digest))
        if existing is not None:
            return existing
        imported = _storage(lambda: self.intake.get(scope, digest))
        archive = _storage(lambda: self.intake.archive(scope, digest))
        validated = validate_bundle(archive)
        try:
            manifest = ActivityManifest.model_validate_json(imported.manifest_json)
        except ValidationError:
            raise CatalogError("invalid_bundle", "Invalid stored activity metadata") from None
        if (
            imported.scope != scope
            or imported.digest != digest
            or imported.state != "validated"
            or validated.digest != digest
            or manifest.descriptor.activity_ref.artifact_digest != digest
        ):
            raise CatalogError("invalid_bundle", "Stored activity integrity check failed")
        # Compare complete normalized manifests, not only a potentially forged digest field.
        validated_manifest = ActivityManifest.model_validate_json(validated.manifest_json)
        if manifest != validated_manifest:
            raise CatalogError("invalid_bundle", "Stored activity integrity check failed")
        try:
            supported = self.capabilities.supports(scope, manifest)
        except (OSError, RuntimeError):
            raise CatalogError("unavailable", "Worker capability provider is unavailable") from None
        if supported is not True:
            raise CatalogError("incompatible_runtime", "No compatible approved worker capability")
        return _storage(lambda: self.repository.publish(imported, archive, actor))

    def get(self, scope: Scope, reference: ActivityRef) -> CatalogEntry:
        self.authorization.authorize(scope, "activity.read")
        return _storage(lambda: self.repository.get(scope, reference))

    def resolve(self, scope: Scope, reference: ActivityRef) -> CatalogEntry:
        entry = self.get(scope, reference)
        if entry.state == "withdrawn":
            raise CatalogError("withdrawn", "Activity is unavailable for new use")
        return entry

    def search(
        self,
        scope: Scope,
        query: str = "",
        category: str | None = None,
        include_deprecated: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CatalogEntry, ...]:
        self.authorization.authorize(scope, "activity.read")
        if (
            not isinstance(query, str)
            or len(query) > 256
            or (category is not None and (not isinstance(category, str) or len(category) > 128))
            or type(include_deprecated) is not bool
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise CatalogError("invalid_input", "Invalid catalog search parameters")
        return _storage(
            lambda: self.repository.search(
                scope, query, category, include_deprecated, limit, offset
            )
        )

    def transition(
        self,
        scope: Scope,
        reference: ActivityRef,
        target: Literal["deprecated", "withdrawn"],
        expected_generation: int,
    ) -> CatalogEntry:
        actor = self.authorization.authorize(scope, "activity.publish")
        if (
            not isinstance(target, str)
            or target not in {"deprecated", "withdrawn"}
            or type(expected_generation) is not int
            or not 1 <= expected_generation <= 2**63 - 1
        ):
            raise CatalogError("invalid_input", "Invalid activity lifecycle request")
        return _storage(
            lambda: self.repository.transition(scope, reference, target, expected_generation, actor)
        )

    def export(
        self, scope: Scope, reference: ActivityRef, usage_id: str | None = None
    ) -> ExportedActivity:
        self.authorization.authorize(scope, "package.export")
        if usage_id is not None and (
            not isinstance(usage_id, str) or not usage_id.strip() or len(usage_id) > 256
        ):
            raise CatalogError("invalid_input", "Invalid server-managed usage reference")
        return _storage(lambda: self.repository.export(scope, reference, usage_id))

    def delete(self, scope: Scope, reference: ActivityRef) -> None:
        actor = self.authorization.authorize(scope, "activity.publish")
        _storage(lambda: self.repository.delete(scope, reference, actor))

    def events(self, scope: Scope, reference: ActivityRef) -> tuple[CatalogEvent, ...]:
        self.authorization.authorize(scope, "activity.read")
        return _storage(lambda: self.repository.events(scope, reference))
