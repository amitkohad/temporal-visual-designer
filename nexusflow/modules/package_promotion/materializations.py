"""Prepare immutable destination releases without publishing code or activating them."""

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.activity_catalog.catalog_types import CatalogRepository
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.package_promotion.materialization_types import (
    MaterializationReceipt,
    MaterializationRepository,
    MaterializationRequest,
    materialization_release_id,
)
from nexusflow.modules.package_promotion.package_format import (
    PackageArchive,
    PackageLimits,
    decode_package,
)
from nexusflow.modules.package_promotion.package_imports import PackageImportRepository
from nexusflow.modules.package_promotion.service import validate_release
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationReadiness,
    ActivationRequest,
)
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import (
    ProjectAuthorization,
    Release,
    VersioningError,
)


class PackageMaterializationService:
    def __init__(
        self,
        imports: PackageImportRepository,
        projects: MaterializationRepository,
        catalog: CatalogRepository,
        authorization: ProjectAuthorization,
        readiness: ActivationReadiness,
        limits: PackageLimits | None = None,
    ) -> None:
        self.imports = imports
        self.projects = projects
        self.catalog = catalog
        self.authorization = authorization
        self.readiness = readiness
        self.limits = limits or PackageLimits()

    def _authorize(self, scope: Scope, *identifiers: str) -> str:
        actor = self.authorization.authorize(scope, "package.import", "package-imports")
        try:
            for identifier in identifiers:
                TypeAdapter(Identifier).validate_python(identifier, strict=True)
        except ValidationError:
            raise ActivationError("invalid_request", "Invalid materialization identifier") from None
        return actor

    @staticmethod
    def _safe[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except VersioningError as error:
            raise ActivationError(error.code, "Destination project is unavailable") from None
        except (OSError, RuntimeError, sqlite3.Error, ValidationError, ValueError, TypeError):
            raise ActivationError(
                "unavailable", "Materialization provider is unavailable"
            ) from None

    def receipt(self, scope: Scope, import_id: str, operation_id: str) -> MaterializationReceipt:
        self._authorize(scope, import_id, operation_id)
        record = self._safe(lambda: self.projects.materialization_receipt(scope, operation_id))
        if record is None or record.import_operation_id != import_id:
            raise ActivationError("not_found", "Materialization operation was not found")
        return record.receipt

    def _approved_artifacts(self, scope: Scope, package: PackageArchive) -> None:
        total = 0
        source_total = 0
        for artifact in package.activities:
            try:
                entry = self.catalog.get(scope, artifact.reference)
                if (
                    entry.scope != scope
                    or entry.reference != artifact.reference
                    or entry.state == "withdrawn"
                ):
                    raise ActivationError(
                        "incompatible_release", "Destination activity is unavailable"
                    )
                target = self.catalog.export(scope, artifact.reference)
                total += len(target.archive)
                if (
                    len(target.archive) > self.limits.maximum_file_bytes
                    or total > self.limits.maximum_source_bytes
                ):
                    raise ActivationError(
                        "incompatible_release", "Destination activity exceeds package limits"
                    )
                validated = validate_bundle(target.archive)
                validated_manifest = ActivityManifest.model_validate_json(validated.manifest_json)
                source_total += sum(item.size for item in validated_manifest.files)
                if source_total > self.limits.maximum_source_bytes:
                    raise ActivationError(
                        "incompatible_release", "Destination sources exceed package limits"
                    )
                source_manifest = ActivityManifest.model_validate_json(artifact.manifest_json)
                entry_manifest = ActivityManifest.model_validate_json(entry.manifest_json)
                export_manifest = ActivityManifest.model_validate_json(target.manifest_json)
                if (
                    target.reference != artifact.reference
                    or validated_manifest.descriptor.activity_ref != artifact.reference
                    or validated_manifest != source_manifest
                    or entry_manifest != source_manifest
                    or export_manifest != source_manifest
                ):
                    raise ActivationError(
                        "incompatible_release", "Destination activity identity does not match"
                    )
            except CatalogError as error:
                if error.code in {
                    "not_found",
                    "withdrawn",
                    "invalid_bundle",
                    "limit_exceeded",
                    "incompatible_runtime",
                }:
                    raise ActivationError(
                        "incompatible_release", "Destination activity is unavailable or invalid"
                    ) from None
                raise ActivationError(
                    "unavailable", "Destination activity provider is unavailable"
                ) from None

    def materialize(
        self, scope: Scope, import_id: str, request: MaterializationRequest
    ) -> MaterializationReceipt:
        actor = self._authorize(scope, import_id, request.operation_id)

        def perform() -> MaterializationReceipt:
            previous = self.projects.materialization_receipt(scope, request.operation_id)
            if previous is not None:
                if previous.import_operation_id != import_id or previous.request != request:
                    raise ActivationError("conflict", "Materialization operation already exists")
                return previous.receipt
            staged = self.imports.download(scope, import_id)
            package = decode_package(staged.archive, self.limits)
            manifest = package.manifest
            if manifest.organization_id != scope.organization_id:
                raise ActivationError(
                    "forbidden", "Package organization does not match destination"
                )
            if manifest.package_digest != staged.metadata.package_digest:
                raise ActivationError("unavailable", "Staged package integrity check failed")
            try:
                head = self.projects.project(scope, manifest.project_id).head_revision
            except VersioningError as error:
                if error.code != "not_found":
                    raise
                head = 0
            if head != request.expected_project_revision:
                # A duplicate may have committed since the initial receipt read.
                duplicate = self.projects.materialization_receipt(scope, request.operation_id)
                if (
                    duplicate is not None
                    and duplicate.request == request
                    and duplicate.import_operation_id == import_id
                ):
                    return duplicate.receipt
                raise ActivationError("conflict", "Destination project revision has changed")
            release = Release(
                scope,
                manifest.project_id,
                materialization_release_id(scope, request.operation_id),
                request.expected_project_revision + 1,
                canonical(package.content.model_dump(mode="json")),
                canonical([ref.model_dump(mode="json") for ref in manifest.activity_refs]),
                manifest.content_digest,
                manifest.executable_digest,
                actor,
                datetime.now(UTC).isoformat(),
            )
            activation = ActivationRequest(
                project_id=release.project_id,
                release_id=release.release_id,
                configuration_revision=request.configuration_revision,
                operation_id=request.operation_id,
                expected_revision=0,
                runtime_version=request.runtime_version,
            )
            validate_release(release, scope, activation)
            self._approved_artifacts(scope, package)
            issues = self.readiness.validate(scope, release, activation)
            if issues:
                raise ActivationError(
                    "incompatible_release", "Destination is not ready for this release", issues
                )
            usage = (
                "materialization-"
                + hashlib.sha256(
                    canonical(
                        [scope.organization_id, scope.environment_id, request.operation_id]
                    ).encode()
                ).hexdigest()
            )
            # Conservative holds precede the atomic project commit; failed attempts may retain artifacts.
            self.readiness.retain(scope, release, usage)
            return self.projects.commit_materialization(
                scope, import_id, request, manifest, release
            ).receipt

        return self._safe(perform)
