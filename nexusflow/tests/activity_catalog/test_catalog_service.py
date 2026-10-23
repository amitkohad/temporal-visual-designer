"""Independent catalog acceptance against durable intake and catalog repositories."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.service import IntakeService
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.tests.activity_catalog.test_intake import source_bundle

SCOPE = Scope(organization_id="acme", environment_id="dev")
FOREIGN = Scope(organization_id="other", environment_id="dev")
UAT = Scope(organization_id="acme", environment_id="uat")


class Authorization:
    def __init__(self) -> None:
        self.permissions = {
            "activity.import",
            "activity.publish",
            "activity.read",
            "package.export",
        }
        self.calls: list[tuple[Scope, str]] = []
        self.actor = "publisher-1"

    def authorize(self, scope: Scope, permission: str) -> str:
        self.calls.append((scope, permission))
        if permission not in self.permissions:
            raise CatalogError("forbidden", "Access denied")
        return self.actor


class Capabilities:
    def __init__(self) -> None:
        self.compatible = True
        self.failed = False
        self.calls: list[tuple[Scope, ActivityManifest]] = []

    def supports(self, scope: Scope, manifest: ActivityManifest) -> bool:
        self.calls.append((scope, manifest))
        if self.failed:
            raise OSError("private worker endpoint token=do-not-leak")
        return self.compatible


@pytest.fixture
def catalog(tmp_path: Path):
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    repository = SQLiteCatalogRepository(tmp_path / "catalog.db")
    authorization = Authorization()
    capabilities = Capabilities()
    importer = IntakeService(intake, authorization)
    service = CatalogService(repository, intake, authorization, capabilities)
    try:
        yield service, importer, repository, authorization, capabilities
    finally:
        repository.close()
        intake.close()


def imported(catalog, *, source: bytes | None = None):
    _, importer, _, _, _ = catalog
    archive, digest = source_bundle() if source is None else source_bundle(source)
    importer.import_bundle(SCOPE, archive)
    return archive, digest


def test_import_is_private_until_explicit_publish_then_palette_has_exact_descriptor(catalog):
    service, _, _, authorization, capabilities = catalog
    archive, digest = imported(catalog)
    assert service.search(SCOPE) == ()
    entry = service.publish(SCOPE, digest)
    assert entry.state == "published"
    assert entry.reference.artifact_digest == digest
    assert entry.publisher_id == "publisher-1"
    manifest = ActivityManifest.model_validate_json(entry.manifest_json)
    assert manifest.descriptor.activity_ref == entry.reference
    assert manifest.descriptor.input_schema["required"] == ["message"]
    assert manifest.descriptor.configuration_schema == {"type": "object"}
    assert manifest.descriptor.documentation
    assert service.search(SCOPE) == (entry,)
    assert service.resolve(SCOPE, entry.reference) == entry
    assert service.export(SCOPE, entry.reference).archive == archive
    assert len(capabilities.calls) == 1
    assert (SCOPE, "activity.publish") in authorization.calls


def test_publish_retry_preserves_identity_audit_and_original_bytes(catalog):
    service, _, _, authorization, capabilities = catalog
    archive, digest = imported(catalog)
    original = service.publish(SCOPE, digest)
    original_events = service.events(SCOPE, original.reference)
    authorization.actor = "different-publisher"
    capabilities.failed = True
    assert service.publish(SCOPE, digest) == original
    assert service.export(SCOPE, original.reference).archive == archive
    assert service.events(SCOPE, original.reference) == original_events
    assert len(capabilities.calls) == 1


def test_runtime_incompatibility_publishes_nothing_and_retry_can_succeed(catalog):
    service, _, _, _, capabilities = catalog
    _, digest = imported(catalog)
    capabilities.compatible = False
    with pytest.raises(CatalogError) as failure:
        service.publish(SCOPE, digest)
    assert failure.value.code == "incompatible_runtime"
    assert service.search(SCOPE) == ()
    capabilities.compatible = True
    assert service.publish(SCOPE, digest).state == "published"


def test_worker_probe_failure_is_sanitized_and_fail_closed(catalog):
    service, _, _, _, capabilities = catalog
    _, digest = imported(catalog)
    capabilities.failed = True
    with pytest.raises(CatalogError) as failure:
        service.publish(SCOPE, digest)
    assert failure.value.code == "unavailable"
    assert "do-not-leak" not in str(failure.value)
    assert service.search(SCOPE) == ()


def test_missing_intake_and_invalid_archive_never_appear_in_catalog(catalog):
    service, importer, _, _, _ = catalog
    _, digest = source_bundle()
    with pytest.raises(CatalogError):
        importer.import_bundle(SCOPE, b"invalid ZIP")
    with pytest.raises(CatalogError) as missing:
        service.publish(SCOPE, digest)
    assert missing.value.code == "not_found"
    assert service.search(SCOPE) == ()


def test_publication_revalidates_stored_archive_before_making_it_visible(catalog, monkeypatch):
    service, importer, _, _, capabilities = catalog
    _, digest = imported(catalog)
    monkeypatch.setattr(importer.repository, "archive", lambda scope, requested: b"corrupted ZIP")
    with pytest.raises(CatalogError):
        service.publish(SCOPE, digest)
    assert service.search(SCOPE) == ()
    assert capabilities.calls == []


def test_publication_rejects_valid_archive_substituted_for_another_digest(catalog, monkeypatch):
    service, importer, _, _, _ = catalog
    _, digest = imported(catalog)
    substituted, other_digest = source_bundle(b"def run(value):\n    return 'substitution'\n")
    assert other_digest != digest
    monkeypatch.setattr(importer.repository, "archive", lambda scope, requested: substituted)
    with pytest.raises(CatalogError):
        service.publish(SCOPE, digest)
    assert service.search(SCOPE) == ()


def test_package_importer_is_not_an_activity_publisher(catalog):
    service, _, _, authorization, _ = catalog
    _, digest = imported(catalog)
    authorization.permissions = {"package.import", "activity.read"}
    with pytest.raises(CatalogError) as forbidden:
        service.publish(SCOPE, digest)
    assert forbidden.value.code == "forbidden"
    assert service.search(SCOPE) == ()


@pytest.mark.parametrize("other_scope", [FOREIGN, UAT])
def test_all_catalog_views_are_scoped_to_organization_and_environment(catalog, other_scope):
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    assert service.search(other_scope) == ()
    for operation in (service.get, service.resolve, service.export):
        with pytest.raises(CatalogError) as missing:
            operation(other_scope, entry.reference)
        assert missing.value.code == "not_found"
    assert service.events(other_scope, entry.reference) == ()
    with pytest.raises(CatalogError) as missing:
        service.publish(other_scope, digest)
    assert missing.value.code == "not_found"


def test_every_public_operation_reauthorizes_after_role_revocation(catalog):
    service, _, _, authorization, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    authorization.permissions.clear()
    operations = [
        lambda: service.publish(SCOPE, digest),
        lambda: service.get(SCOPE, entry.reference),
        lambda: service.resolve(SCOPE, entry.reference),
        lambda: service.search(SCOPE),
        lambda: service.export(SCOPE, entry.reference),
        lambda: service.events(SCOPE, entry.reference),
        lambda: service.transition(SCOPE, entry.reference, "deprecated", entry.generation),
        lambda: service.delete(SCOPE, entry.reference),
    ]
    for operation in operations:
        before = len(authorization.calls)
        with pytest.raises(CatalogError) as forbidden:
            operation()
        assert forbidden.value.code == "forbidden"
        assert len(authorization.calls) == before + 1


def test_deprecation_hides_new_selection_preserves_pins_and_export(catalog):
    service, _, _, authorization, _ = catalog
    archive, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    authorization.actor = "lifecycle-admin"
    deprecated = service.transition(SCOPE, entry.reference, "deprecated", entry.generation)
    assert deprecated.generation > entry.generation
    assert deprecated.publisher_id == entry.publisher_id
    assert deprecated.updated_by == "lifecycle-admin"
    assert deprecated.manifest_json == entry.manifest_json
    assert service.search(SCOPE) == ()
    assert service.search(SCOPE, include_deprecated=True) == (deprecated,)
    assert service.resolve(SCOPE, entry.reference) == deprecated
    assert service.export(SCOPE, entry.reference).archive == archive
    assert len(service.events(SCOPE, entry.reference)) == 2


def test_stale_lifecycle_update_cannot_overwrite_current_state(catalog):
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    deprecated = service.transition(SCOPE, entry.reference, "deprecated", entry.generation)
    with pytest.raises(CatalogError) as conflict:
        service.transition(SCOPE, entry.reference, "withdrawn", entry.generation)
    assert conflict.value.code == "conflict"
    assert service.get(SCOPE, entry.reference) == deprecated


def test_withdrawal_preserves_existing_hold_but_blocks_new_use_and_resurrection(catalog):
    service, _, repository, _, capabilities = catalog
    archive, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    repository.retain(SCOPE, entry.reference, "execution-123")
    withdrawn = service.transition(SCOPE, entry.reference, "withdrawn", entry.generation)
    assert service.search(SCOPE, include_deprecated=True) == ()
    with pytest.raises(CatalogError):
        service.resolve(SCOPE, entry.reference)
    with pytest.raises(CatalogError):
        service.export(SCOPE, entry.reference)
    with pytest.raises(CatalogError):
        service.export(SCOPE, entry.reference, usage_id="unretained-execution")
    with pytest.raises(CatalogError):
        repository.retain(SCOPE, entry.reference, "new-execution")
    repository.retain(SCOPE, entry.reference, "execution-123")
    assert service.export(SCOPE, entry.reference, usage_id="execution-123").archive == archive
    capabilities.failed = True
    assert service.publish(SCOPE, digest) == withdrawn
    assert service.get(SCOPE, entry.reference).state == "withdrawn"


def test_referenced_artifact_cannot_be_deleted_and_purged_version_cannot_republish(catalog):
    service, _, repository, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    repository.retain(SCOPE, entry.reference, "project-release-123")
    service.transition(SCOPE, entry.reference, "withdrawn", entry.generation)
    with pytest.raises(CatalogError):
        service.delete(SCOPE, entry.reference)
    repository.release_reference(SCOPE, entry.reference, "project-release-123")
    service.delete(SCOPE, entry.reference)
    with pytest.raises(CatalogError):
        service.export(SCOPE, entry.reference)
    with pytest.raises(CatalogError):
        service.publish(SCOPE, digest)
    assert service.search(SCOPE, include_deprecated=True) == ()


def test_published_activity_cannot_be_deleted_without_withdrawal(catalog):
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    with pytest.raises(CatalogError):
        service.delete(SCOPE, entry.reference)
    assert service.resolve(SCOPE, entry.reference) == entry


def test_changed_bytes_cannot_replace_existing_published_version(catalog):
    service, importer, _, _, _ = catalog
    archive, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    changed, _ = source_bundle(b"def run(value):\n    return 'changed'\n")
    with pytest.raises(CatalogError) as conflict:
        importer.import_bundle(SCOPE, changed)
    assert conflict.value.code == "conflict"
    assert service.resolve(SCOPE, entry.reference) == entry
    assert service.export(SCOPE, entry.reference).archive == archive


def test_search_is_categorized_and_supports_metadata_queries_and_pagination(catalog):
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    assert service.search(SCOPE, query="notification", category="notifications") == (entry,)
    assert service.search(SCOPE, query="missing") == ()
    assert service.search(SCOPE, category="finance") == ()
    assert service.search(SCOPE, limit=1, offset=1) == ()


def test_catalog_metadata_and_artifact_access_never_execute_python(catalog, tmp_path):
    service, _, _, _, _ = catalog
    marker = tmp_path / "side-effect.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def run(value):\n    return value\n"
    ).encode()
    archive, digest = imported(catalog, source=source)
    entry = service.publish(SCOPE, digest)
    service.search(SCOPE)
    service.resolve(SCOPE, entry.reference)
    assert service.export(SCOPE, entry.reference).archive == archive
    assert not marker.exists()


def test_export_is_self_contained_and_preserves_validated_manifest(catalog):
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    exported = service.export(SCOPE, entry.reference)
    assert exported.reference == entry.reference
    assert exported.manifest_json == entry.manifest_json
    with zipfile.ZipFile(io.BytesIO(exported.archive)) as archive:
        manifest = ActivityManifest.model_validate_json(archive.read("manifest.json"))
        assert manifest.descriptor.activity_ref == exported.reference
        assert json.loads(exported.manifest_json) == manifest.model_dump(mode="json")
        assert archive.read("src/activity.py")
