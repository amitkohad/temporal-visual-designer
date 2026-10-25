"""Intake stores original evidence atomically without creating executable releases."""

import io
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.package_format import decode_package, encode_package
from nexusflow.modules.package_promotion.package_imports import (
    PackageImportService,
    SQLitePackageImportRepository,
)
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.tests.package_promotion.test_package_format import release_with_activity

SCOPE = Scope(organization_id="acme", environment_id="uat")


class Authorization:
    actor = "trusted-actor"
    denied = False

    def __init__(self):
        self.calls = []

    def authorize(self, scope, permission, resource):
        self.calls.append((scope, permission, resource))
        if self.denied:
            raise ActivationError("forbidden", "Denied")
        return self.actor


@pytest.fixture
def package():
    release, activities = release_with_activity()
    return encode_package(release, activities)


@pytest.fixture
def system(tmp_path):
    repository = SQLitePackageImportRepository(str(tmp_path / "imports.db"))
    authorization = Authorization()
    return repository, authorization, PackageImportService(repository, authorization)


def repack(archive):
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as target:
            for name in reversed(source.namelist()):
                target.writestr(name, source.read(name))
    return result.getvalue()


def test_stage_reopen_and_repacked_retry_preserve_original(tmp_path, package):
    path = str(tmp_path / "imports.db")
    repository = SQLitePackageImportRepository(path, maximum_imports_per_scope=1)
    authorization = Authorization()
    service = PackageImportService(repository, authorization)
    original = service.stage(SCOPE, "import-one", package.archive)
    assert original.actor_id == "trusted-actor"
    assert original.state == "staged"
    authorization.actor = "second-actor"
    wrapper = repack(package.archive)
    assert wrapper != package.archive
    assert decode_package(wrapper).manifest == package.manifest
    assert service.stage(SCOPE, "import-one", wrapper) == original
    reopened = PackageImportService(SQLitePackageImportRepository(path), authorization)
    assert reopened.get(SCOPE, "import-one") == original
    assert reopened.download(SCOPE, "import-one").archive == package.archive
    assert reopened.manifest(SCOPE, "import-one") == package.manifest
    with pytest.raises(ActivationError, match="quota") as error:
        service.stage(SCOPE, "another", package.archive)
    assert error.value.code == "import_limit"


def test_changed_operation_conflicts_and_does_not_append(system, package):
    repository, _, service = system
    first = service.stage(SCOPE, "first", package.archive)
    release, activities = release_with_activity()
    changed = encode_package(replace(release, release_id="other-release"), activities)
    with pytest.raises(ActivationError) as error:
        service.stage(SCOPE, "first", changed.archive)
    assert error.value.code == "conflict"
    assert service.list(SCOPE).items == (first,)
    assert repository.download(SCOPE, "first").archive == package.archive


@pytest.mark.parametrize(
    "method,args",
    [
        ("stage", ("bad/id", b"bad")),
        ("get", ("bad/id",)),
        ("manifest", ("bad/id",)),
        ("download", ("bad/id",)),
        ("list", ()),
    ],
)
def test_fresh_authorization_precedes_validation(system, method, args):
    _, authorization, service = system
    authorization.denied = True
    with pytest.raises(ActivationError) as error:
        getattr(service, method)(SCOPE, *args)
    assert error.value.code == "forbidden"
    assert authorization.calls == [(SCOPE, "package.import", "package-imports")]


def test_scope_and_org_isolation(system, package):
    _, _, service = system
    service.stage(SCOPE, "first", package.archive)
    other_env = Scope(organization_id="acme", environment_id="prod")
    assert service.list(other_env).items == ()
    with pytest.raises(ActivationError) as error:
        service.get(other_env, "first")
    assert error.value.code == "not_found"
    other_org = Scope(organization_id="other", environment_id="uat")
    with pytest.raises(ActivationError) as error:
        service.stage(other_org, "first", package.archive)
    assert error.value.code == "forbidden"
    assert service.list(other_org).items == ()


def test_keyset_stability_and_metadata_only_queries(system, package):
    repository, _, service = system
    for key in ("one", "two", "three"):
        service.stage(SCOPE, key, package.archive)
    statements = []
    repository._db.set_trace_callback(statements.append)
    page = service.list(SCOPE, limit=2)
    assert [item.revision for item in page.items] == [3, 2]
    assert page.next_before_revision == 2
    assert all(
        "archive" not in query.lower() and "manifest" not in query.lower()
        for query in statements
        if query.startswith("SELECT")
    )
    service.stage(SCOPE, "four", package.archive)
    tail = service.list(SCOPE, limit=2, before_revision=page.next_before_revision)
    assert [item.revision for item in tail.items] == [1]
    assert tail.next_before_revision is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"limit": "1"},
        {"before_revision": True},
        {"before_revision": 0},
        {"before_revision": 2**63},
    ],
)
def test_pagination_rejects_unsafe_values(system, kwargs):
    _, _, service = system
    with pytest.raises(ActivationError) as error:
        service.list(SCOPE, **kwargs)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("quota", [True, 0, -1, 1.5])
def test_quota_configuration_is_positive_exact_integer(tmp_path, quota):
    path = tmp_path / "absent.db"
    with pytest.raises(ValueError):
        SQLitePackageImportRepository(str(path), maximum_imports_per_scope=quota)
    assert not path.exists()


@pytest.mark.parametrize(
    "column,value,method",
    [
        ("archive", b"corrupt", "download"),
        ("physical_size", 0, "download"),
        ("physical_sha", "bad", "download"),
        ("manifest", "{}", "manifest"),
        ("project_id", "other", "manifest"),
    ],
)
def test_corrupted_storage_never_returns_content(system, package, column, value, method):
    repository, _, service = system
    service.stage(SCOPE, "first", package.archive)
    repository._db.execute(f"UPDATE package_imports SET {column}=?", (value,))
    repository._db.commit()
    with pytest.raises(ActivationError) as error:
        getattr(service, method)(SCOPE, "first")
    assert error.value.code == "unavailable"


def test_atomic_quota_across_connections(tmp_path, package):
    path = str(tmp_path / "imports.db")
    repositories = [
        SQLitePackageImportRepository(path, maximum_imports_per_scope=1) for _ in range(2)
    ]

    def write(index):
        try:
            return repositories[index].put(SCOPE, f"op-{index}", package, "actor")
        except ActivationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, range(2)))
    assert sum(result == "import_limit" for result in results) == 1
    assert len(repositories[0].list(SCOPE).items) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM package_imports").fetchone()[0] == 1


def test_missing_and_invalid_archive_leave_store_clean(system):
    _, _, service = system
    with pytest.raises(ActivationError):
        service.stage(SCOPE, "first", b"invalid")
    assert service.list(SCOPE).items == ()
    for method in (service.get, service.manifest, service.download):
        with pytest.raises(ActivationError) as error:
            method(SCOPE, "missing")
        assert error.value.code == "not_found"


def test_closed_store_is_safe_unavailable(system, package):
    repository, _, service = system
    service.stage(SCOPE, "first", package.archive)
    repository.close()
    for method, args in (
        (service.get, ("first",)),
        (service.list, ()),
        (service.manifest, ("first",)),
        (service.download, ("first",)),
        (service.stage, ("another", package.archive)),
    ):
        with pytest.raises(ActivationError) as error:
            method(SCOPE, *args)
        assert error.value.code == "unavailable"


def test_lost_acknowledgement_retry_preserves_first_write(system, package):
    repository, authorization, service = system
    real_put = repository.put

    def lost_ack(*args):
        real_put(*args)
        raise OSError("lost acknowledgement")

    repository.put = lost_ack
    with pytest.raises(ActivationError) as error:
        service.stage(SCOPE, "first", package.archive)
    assert error.value.code == "unavailable"
    repository.put = real_put
    authorization.actor = "retry-actor"
    result = service.stage(SCOPE, "first", package.archive)
    assert result.revision == 1
    assert result.actor_id == "trusted-actor"
    assert len(service.list(SCOPE).items) == 1


def test_listing_includes_largest_valid_revision(system, package):
    repository, _, service = system
    service.stage(SCOPE, "first", package.archive)
    repository._db.execute("UPDATE package_imports SET revision=?", (2**63 - 1,))
    repository._db.commit()
    assert service.list(SCOPE).items[0].revision == 2**63 - 1
    assert service.list(SCOPE, before_revision=2**63 - 1).items == ()


def test_repository_rejects_cross_org_package_without_writes(system, package):
    repository, _, _ = system
    other = Scope(organization_id="other", environment_id="uat")
    with pytest.raises(ActivationError) as error:
        repository.put(other, "first", package, "actor")
    assert error.value.code == "forbidden"
    assert repository.list(other).items == ()
