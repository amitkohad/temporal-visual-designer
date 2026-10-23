"""Independent acceptance tests for private, non-executing activity bundle intake."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from nexusflow.contracts.models import ActivityDescriptor, Scope
from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.service import IntakeService
from nexusflow.modules.activity_catalog.types import (
    ActivityManifest,
    BundleFile,
    CatalogError,
    PythonRuntime,
    ValidatedBundle,
)

SCOPE = Scope(organization_id="acme", environment_id="dev")
FOREIGN = Scope(organization_id="other", environment_id="dev")


class Authorization:
    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[tuple[Scope, str]] = []

    def authorize(self, scope: Scope, permission: str) -> str:
        self.calls.append((scope, permission))
        if not self.allowed:
            raise CatalogError("forbidden", "Access denied")
        assert permission == "activity.import"
        return "activity-publisher"


def source_bundle(
    source: bytes = b"def run(value):\n    return value\n",
    *,
    reverse: bool = False,
) -> tuple[bytes, str]:
    descriptor_data = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "activity-descriptor.json").read_text()
    )
    descriptor_data["connection_requirements"] = []
    descriptor = ActivityDescriptor.model_validate_json(json.dumps(descriptor_data))
    manifest = ActivityManifest(
        schema_version="0.1",
        descriptor=descriptor,
        runtime=PythonRuntime(language="python", python_version="3.12", entrypoint="activity:run"),
        files=(
            BundleFile(
                path="src/activity.py",
                digest="sha256:" + hashlib.sha256(source).hexdigest(),
                size=len(source),
            ),
        ),
    )
    digest = bundle_digest(manifest)
    descriptor = descriptor.model_copy(
        update={
            "activity_ref": descriptor.activity_ref.model_copy(update={"artifact_digest": digest})
        }
    )
    manifest = manifest.model_copy(update={"descriptor": descriptor})
    items = [("manifest.json", manifest.model_dump_json().encode()), ("src/activity.py", source)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in reversed(items) if reverse else items:
            archive.writestr(name, data)
    return buffer.getvalue(), digest


@pytest.fixture
def system(tmp_path: Path):
    repository = SQLiteIntakeRepository(tmp_path / "catalog.db")
    authorization = Authorization()
    service = IntakeService(repository, authorization)
    try:
        yield service, repository, authorization
    finally:
        repository.close()


def test_source_import_persists_and_remains_validated_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "catalog.db"
    archive, digest = source_bundle()
    repository = SQLiteIntakeRepository(path)
    try:
        imported = IntakeService(repository, Authorization()).import_bundle(SCOPE, archive)
        assert imported.digest == digest
        assert imported.state == "validated"
        assert imported.author_id == "activity-publisher"
        assert imported.created_at
    finally:
        repository.close()
    reopened = SQLiteIntakeRepository(path)
    try:
        service = IntakeService(reopened, Authorization())
        assert service.read_import(SCOPE, digest) == imported
        assert service.download_import(SCOPE, digest) == archive
    finally:
        reopened.close()


def test_repacked_identical_content_returns_original_import(system) -> None:
    service, _, authorization = system
    archive, digest = source_bundle()
    repacked, repeated_digest = source_bundle(reverse=True)
    assert archive != repacked
    assert digest == repeated_digest
    first = service.import_bundle(SCOPE, archive)
    assert service.import_bundle(SCOPE, repacked) == first
    assert service.download_import(SCOPE, digest) == archive
    assert authorization.calls == [(SCOPE, "activity.import")] * 3


def test_changed_content_cannot_overwrite_activity_version(system) -> None:
    service, _, _ = system
    archive, digest = source_bundle()
    replacement, replacement_digest = source_bundle(b"def run(value):\n    return None\n")
    original = service.import_bundle(SCOPE, archive)
    with pytest.raises(CatalogError) as error:
        service.import_bundle(SCOPE, replacement)
    assert error.value.code == "conflict"
    assert service.read_import(SCOPE, digest) == original
    with pytest.raises(CatalogError) as missing:
        service.read_import(SCOPE, replacement_digest)
    assert missing.value.code == "not_found"


def test_foreign_scope_cannot_read_or_download_import(system) -> None:
    service, _, _ = system
    archive, digest = source_bundle()
    service.import_bundle(SCOPE, archive)
    for operation in (service.read_import, service.download_import):
        with pytest.raises(CatalogError) as error:
            operation(FOREIGN, digest)
        assert error.value.code == "not_found"


def test_failed_validation_does_not_create_a_record(system) -> None:
    service, repository, _ = system
    _, digest = source_bundle()
    with pytest.raises(CatalogError):
        service.import_bundle(SCOPE, b"not a ZIP archive")
    with pytest.raises(CatalogError) as error:
        repository.get(SCOPE, digest)
    assert error.value.code == "not_found"


def test_import_never_executes_source_module(system, tmp_path: Path) -> None:
    service, _, _ = system
    marker = tmp_path / "executed.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "def run(value):\n    return value\n"
    ).encode()
    archive, digest = source_bundle(source)
    assert service.import_bundle(SCOPE, archive).digest == digest
    assert not marker.exists()


def test_package_import_permission_alone_does_not_allow_activity_intake(tmp_path: Path) -> None:
    class PackageOnly:
        def authorize(self, scope: Scope, permission: str) -> str:
            if permission != "package.import":
                raise CatalogError("forbidden", "Access denied")
            return "business-author"

    repository = SQLiteIntakeRepository(tmp_path / "catalog.db")
    try:
        service = IntakeService(repository, PackageOnly())
        archive, _ = source_bundle()
        with pytest.raises(CatalogError) as error:
            service.import_bundle(SCOPE, archive)
        assert error.value.code == "forbidden"
    finally:
        repository.close()


def test_failed_commit_rolls_back_all_import_metadata_and_bytes(tmp_path: Path) -> None:
    class FailingRepository(SQLiteIntakeRepository):
        def _insert(self, scope: Scope, bundle: ValidatedBundle, author_id: str):
            super()._insert(scope, bundle, author_id)
            raise RuntimeError("private storage details")

    path = tmp_path / "catalog.db"
    archive, digest = source_bundle()
    repository = FailingRepository(path)
    try:
        with pytest.raises(CatalogError) as error:
            IntakeService(repository, Authorization()).import_bundle(SCOPE, archive)
        assert error.value.code == "unavailable"
        assert "private storage details" not in str(error.value)
    finally:
        repository.close()
    reopened = SQLiteIntakeRepository(path)
    try:
        for operation in (reopened.get, reopened.archive):
            with pytest.raises(CatalogError) as missing:
                operation(SCOPE, digest)
            assert missing.value.code == "not_found"
        assert (
            IntakeService(reopened, Authorization()).import_bundle(SCOPE, archive).digest == digest
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ["read_import", "download_import", "import_bundle"])
def test_permissions_are_checked_again_after_revocation(system, operation: str) -> None:
    service, _, authorization = system
    archive, digest = source_bundle()
    service.import_bundle(SCOPE, archive)
    authorization.allowed = False
    argument = archive if operation == "import_bundle" else digest
    with pytest.raises(CatalogError) as error:
        getattr(service, operation)(SCOPE, argument)
    assert error.value.code == "forbidden"
    assert authorization.calls == [(SCOPE, "activity.import")] * 2
