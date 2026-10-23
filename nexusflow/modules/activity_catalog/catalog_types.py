"""Frozen catalog lifecycle, export and replaceable persistence/capability ports."""

from dataclasses import dataclass
from typing import Literal, Protocol

from nexusflow.contracts.models import ActivityRef, Scope
from nexusflow.modules.activity_catalog.types import ActivityManifest, ImportedBundle

CatalogState = Literal["published", "deprecated", "withdrawn"]


@dataclass(frozen=True)
class CatalogEntry:
    scope: Scope
    reference: ActivityRef
    manifest_json: str
    state: CatalogState
    generation: int
    publisher_id: str
    published_at: str
    updated_by: str
    updated_at: str


@dataclass(frozen=True)
class ExportedActivity:
    reference: ActivityRef
    manifest_json: str
    archive: bytes


@dataclass(frozen=True)
class CatalogEvent:
    reference: ActivityRef
    action: str
    actor_id: str
    timestamp: str


class RuntimeCapabilities(Protocol):
    def supports(self, scope: Scope, manifest: ActivityManifest) -> bool:
        """Check destination worker compatibility without executing user Python."""
        ...


class CatalogRepository(Protocol):
    def by_digest(self, scope: Scope, digest: str) -> CatalogEntry | None: ...
    def publish(self, imported: ImportedBundle, archive: bytes, actor_id: str) -> CatalogEntry: ...
    def get(self, scope: Scope, reference: ActivityRef) -> CatalogEntry: ...
    def search(
        self,
        scope: Scope,
        query: str = "",
        category: str | None = None,
        include_deprecated: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[CatalogEntry, ...]: ...
    def transition(
        self,
        scope: Scope,
        reference: ActivityRef,
        target: Literal["deprecated", "withdrawn"],
        expected_generation: int,
        actor_id: str,
    ) -> CatalogEntry: ...
    def retain(self, scope: Scope, reference: ActivityRef, usage_id: str) -> None:
        """Internal: hold before release/run use; withdrawn permits only existing hold retries."""
        ...

    def release_reference(self, scope: Scope, reference: ActivityRef, usage_id: str) -> None: ...
    def export(
        self, scope: Scope, reference: ActivityRef, usage_id: str | None = None
    ) -> ExportedActivity:
        """Withdrawn artifacts require an existing server-managed usage reference."""
        ...

    def delete(self, scope: Scope, reference: ActivityRef, actor_id: str) -> None:
        """Only withdrawn and unreferenced; retain immutable version tombstone."""
        ...

    def events(self, scope: Scope, reference: ActivityRef) -> tuple[CatalogEvent, ...]: ...
