"""Real provider composition, fresh identity policy and immutable descriptor copies."""

import json
from dataclasses import replace

import pytest

from nexusflow.apps.catalog_adapters import ScopedCatalog
from nexusflow.modules.activity_catalog.authorization import IdentityCatalogAuthorization
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.service import IntakeService
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import VersioningError
from nexusflow.tests.activity_catalog.test_catalog_service import (
    SCOPE,  # noqa: F401
    Capabilities,
    catalog,  # noqa: F401
    imported,
)
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.designer_forms.test_acceptance import project
from nexusflow.tests.identity.test_service import identity  # noqa: F401


class ProjectAuthorization:
    def authorize(self, scope, permission, project_id):
        return "business-author"


def test_catalog_drives_real_project_validation_and_pinned_release(catalog, tmp_path):  # noqa: F811
    service, _, _, _, _ = catalog
    _, digest = imported(catalog)
    entry = service.publish(SCOPE, digest)
    adapter = ScopedCatalog(service, SCOPE)
    payload = project({"message": "Welcome"})
    reference = entry.reference.model_dump(mode="json")
    payload["activity_refs"] = [reference]
    payload["workflows"][0]["nodes"][1]["activity_ref"] = reference
    repository = SQLiteProjectRepository(tmp_path / "projects.db")
    try:
        projects = ProjectService(
            repository, ProjectAuthorization(), AuthoringValidator(adapter), adapter
        )
        revision = projects.create(SCOPE, "hr", "HR", json.dumps(payload))
        release = projects.publish(SCOPE, "hr", revision.number, "first")
        assert json.loads(release.artifacts_json) == [reference]
        descriptor = adapter.descriptor(entry.reference)
        descriptor.input_schema["type"] = "integer"
        assert adapter.descriptor(entry.reference).input_schema["type"] == "object"
        deprecated = service.transition(SCOPE, entry.reference, "deprecated", entry.generation)
        assert service.search(SCOPE) == ()
        assert (
            projects.publish(SCOPE, "hr", revision.number, "second").executable_digest
            == release.executable_digest
        )
        service.transition(SCOPE, entry.reference, "withdrawn", deprecated.generation)
        assert adapter.descriptor(entry.reference) is None
        assert adapter.resolve(entry.reference) is None
        with pytest.raises(VersioningError) as error:
            projects.publish(SCOPE, "hr", revision.number, "withdrawn")
        assert error.value.code == "validation_failed"
        assert repository.published(SCOPE, "hr", "withdrawn") is None
        assert projects.get_release(SCOPE, "hr", release.release_id) == release
    finally:
        repository.close()


def test_business_author_can_read_palette_but_cannot_publish_code(identity, tmp_path):  # noqa: F811
    identity_repo, scope, grant, keys, token = identity
    intake = SQLiteIntakeRepository(tmp_path / "intake.db")
    repository = SQLiteCatalogRepository(tmp_path / "catalog.db")
    auth = IdentityCatalogAuthorization(IdentityService(identity_repo, keys), token())
    service = CatalogService(repository, intake, auth, Capabilities())
    try:
        archive, digest = source_bundle()
        assert service.search(scope) == ()
        with pytest.raises(IdentityError):
            service.publish(scope, digest)
        identity_repo.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
        IntakeService(intake, auth).import_bundle(scope, archive)
        entry = service.publish(scope, digest)
        identity_repo.save_grant(grant, expected_revision=2)
        assert service.search(scope) == (entry,)
        assert service.export(scope, entry.reference).archive == archive
        with pytest.raises(IdentityError):
            service.publish(scope, digest)
        with pytest.raises(IdentityError):
            service.transition(scope, entry.reference, "withdrawn", 1)
        identity_repo.revoke_grant(scope, grant.grant_id, expected_revision=3)
        with pytest.raises(IdentityError):
            service.search(scope)
    finally:
        repository.close()
        intake.close()
