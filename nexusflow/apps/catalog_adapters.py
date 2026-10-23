"""Scope-bound public catalog adapters for authoring and project publication."""

from nexusflow.contracts.models import ActivityDescriptor, ActivityRef, Scope
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.modules.project_versioning.types import ResolvedArtifact


class ScopedCatalog:
    def __init__(self, catalog: CatalogService, scope: Scope) -> None:
        self.catalog = catalog
        self.scope = scope

    def _manifest(self, reference: ActivityRef) -> ActivityManifest | None:
        try:
            entry = self.catalog.resolve(self.scope, reference)
        except CatalogError as error:
            if error.code in {"not_found", "withdrawn"}:
                return None
            if error.code == "unavailable":
                raise OSError("Activity catalog is unavailable") from None
            raise
        return ActivityManifest.model_validate_json(entry.manifest_json)

    def descriptor(self, reference: ActivityRef) -> ActivityDescriptor | None:
        manifest = self._manifest(reference)
        return None if manifest is None else manifest.descriptor

    def resolve(self, reference: ActivityRef) -> ResolvedArtifact | None:
        manifest = self._manifest(reference)
        # Vendored Python dependencies are sealed in this artifact's file closure.
        return None if manifest is None else ResolvedArtifact(manifest.descriptor.activity_ref)
