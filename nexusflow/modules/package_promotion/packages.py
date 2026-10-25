"""Authorized, side-effect-free export and inspection of immutable releases."""

import json
import sqlite3
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import ActivityRef, Identifier, Scope
from nexusflow.modules.activity_catalog.catalog_types import ExportedActivity
from nexusflow.modules.package_promotion.package_format import (
    PackageArchive,
    PackageLimits,
    PackageManifest,
    decode_package,
    encode_package,
)
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.types import (
    ProjectAuthorization,
    ProjectRepository,
    Release,
)


class PackageCatalog(Protocol):
    def export(self, scope: Scope, reference: ActivityRef) -> ExportedActivity: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate object key")
        result[key] = value
    return result


class ProjectPackageService:
    def __init__(
        self,
        projects: ProjectRepository,
        catalog: PackageCatalog,
        authorization: ProjectAuthorization,
        limits: PackageLimits | None = None,
    ) -> None:
        self.projects = projects
        self.catalog = catalog
        self.authorization = authorization
        self.limits = limits or PackageLimits()

    def export(self, scope: Scope, project_id: str, release_id: str) -> PackageArchive:
        self.authorization.authorize(scope, "package.export", project_id)
        try:
            TypeAdapter(Identifier).validate_python(project_id)
            TypeAdapter(Identifier).validate_python(release_id)
        except ValidationError as error:
            raise ActivationError("invalid_input", "Invalid release identifier") from error
        try:
            release = self.projects.release(scope, project_id, release_id)
        except (OSError, sqlite3.Error, RuntimeError) as error:
            raise ActivationError("unavailable", "Release storage is unavailable") from error
        if not isinstance(release, Release):
            raise ActivationError("unavailable", "Release storage returned an invalid record")
        if (release.scope, release.project_id, release.release_id) != (
            scope,
            project_id,
            release_id,
        ):
            raise ActivationError("invalid_package", "Release identity is inconsistent")
        if not isinstance(release.content_json, str) or not isinstance(release.artifacts_json, str):
            raise ActivationError("invalid_package", "Release content is invalid")
        try:
            if len(release.content_json.encode("utf-8")) > self.limits.maximum_project_bytes:
                raise ActivationError("package_limit", "Project exceeds package limits")
            if len(release.artifacts_json.encode("utf-8")) > self.limits.maximum_manifest_bytes:
                raise ActivationError("package_limit", "Artifact index exceeds package limits")
            raw = json.loads(release.artifacts_json, object_pairs_hook=_unique_object)
            if not isinstance(raw, list):
                raise ValueError("Invalid artifact index")
            if len(raw) > min(100, self.limits.maximum_activities):
                raise ActivationError("package_limit", "Too many package activities")
            references = tuple(ActivityRef.model_validate(item) for item in raw)
            keys = [(ref.activity_id, ref.version) for ref in references]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate artifact index")
        except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise ActivationError("invalid_package", "Release artifact index is invalid") from error
        activities: list[ExportedActivity] = []
        archive_bytes = 0
        for reference in references:
            try:
                activity = self.catalog.export(scope, reference)
            except (OSError, sqlite3.Error, RuntimeError) as error:
                raise ActivationError("unavailable", "Activity storage is unavailable") from error
            if (
                not isinstance(activity, ExportedActivity)
                or not isinstance(activity.archive, bytes)
                or not isinstance(activity.manifest_json, str)
            ):
                raise ActivationError("invalid_package", "Activity export is invalid")
            if activity.reference != reference:
                raise ActivationError("invalid_package", "Activity identity is inconsistent")
            archive_bytes += len(activity.archive)
            if (
                len(activity.archive) > self.limits.maximum_file_bytes
                or archive_bytes + len(release.content_json.encode("utf-8"))
                > self.limits.maximum_expanded_bytes
            ):
                raise ActivationError("package_limit", "Activity sources exceed package limits")
            activities.append(activity)
        return encode_package(release, tuple(activities), self.limits)

    def validate(self, scope: Scope, archive: bytes) -> PackageManifest:
        self.authorization.authorize(scope, "package.import", "packages")
        package = decode_package(archive, self.limits)
        if package.manifest.organization_id != scope.organization_id:
            raise ActivationError("forbidden", "Package organization does not match destination")
        return package.manifest
