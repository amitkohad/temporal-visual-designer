"""Portable dependency export and transactional publication failure boundaries."""

import json

import pytest
from jsonschema import Draft202012Validator

from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.service import IntakeService
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.tests.activity_catalog.test_catalog_service import (
    SCOPE,
    Authorization,
    catalog,  # noqa: F401
    imported,  # noqa: F401
)
from nexusflow.tests.activity_catalog.test_validation import archive


def test_complete_vendored_export_imports_without_original_registry(catalog, tmp_path):  # noqa: F811
    service, importer, _, _, _ = catalog
    runtime = {
        "language": "python",
        "python_version": "3.12",
        "entrypoint": "activity:run",
        "dependencies": [
            {"name": "helper", "version": "1.2.3", "files": ["src/vendor/helper/mod.py"]}
        ],
    }
    data = archive(
        {"src/activity.py": b"pass", "src/vendor/helper/mod.py": b"pass"}, runtime=runtime
    )
    candidate = importer.import_bundle(SCOPE, data)
    entry = service.publish(SCOPE, candidate.digest)
    exported = service.export(SCOPE, entry.reference)
    destination = SQLiteIntakeRepository(tmp_path / "uat.db")
    try:
        result = IntakeService(destination, Authorization()).import_bundle(
            Scope(organization_id=SCOPE.organization_id, environment_id="uat"), exported.archive
        )
        assert result.digest == entry.reference.artifact_digest
        manifest = ActivityManifest.model_validate_json(result.manifest_json)
        assert {item.path for item in manifest.files} == {
            "src/activity.py",
            "src/vendor/helper/mod.py",
        }
    finally:
        destination.close()


def test_failed_publication_audit_has_no_discoverable_version_then_retry_succeeds(
    catalog,  # noqa: F811
    monkeypatch,
):  # noqa: F811
    service, _, repo, _, _ = catalog
    _, digest = imported(catalog)
    original = repo._event

    def fail(*args):
        raise OSError("private-storage-details")

    monkeypatch.setattr(repo, "_event", fail)
    with pytest.raises(CatalogError) as error:
        service.publish(SCOPE, digest)
    assert error.value.code == "unavailable"
    assert "private-storage-details" not in str(error.value)
    assert service.search(SCOPE) == ()
    monkeypatch.setattr(repo, "_event", original)
    entry = service.publish(SCOPE, digest)
    assert service.events(SCOPE, entry.reference)[0].action == "published"


@pytest.mark.parametrize("target", ["published", "deleted", [], None])
def test_invalid_lifecycle_request_cannot_mutate_publication(catalog, target):  # noqa: F811
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    with pytest.raises(CatalogError) as error:
        service.transition(SCOPE, entry.reference, target, 1)
    assert error.value.code == "invalid_input"
    assert service.get(SCOPE, entry.reference) == entry


def test_catalog_schema_export_is_portable(tmp_path, monkeypatch):
    from nexusflow.modules.activity_catalog.export import main

    monkeypatch.chdir(tmp_path)
    main()
    schema = json.loads(
        (tmp_path / "artifacts/schemas/0.1/activity-catalog-entry.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["state"]["enum"] == ["published", "deprecated", "withdrawn"]
