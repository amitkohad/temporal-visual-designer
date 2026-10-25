"""Atomic target revision/release persistence, independent of HTTP/readiness providers."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.materialization_types import (
    MaterializationRepository,
    MaterializationRequest,
    materialization_release_id,
)
from nexusflow.modules.package_promotion.package_format import PackageManifest, encode_package
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.types import Release, VersioningError
from nexusflow.tests.package_promotion.test_package_format import release_with_activity

SCOPE = Scope(organization_id="acme", environment_id="uat")


def candidate(
    operation: str = "materialize", expected: int = 0
) -> tuple[MaterializationRequest, PackageManifest, Release]:
    source, activities = release_with_activity()
    manifest = encode_package(source, activities).manifest
    request = MaterializationRequest(
        operation_id=operation,
        expected_project_revision=expected,
        configuration_revision="config-1",
    )
    target = replace(
        source,
        scope=SCOPE,
        release_id=materialization_release_id(SCOPE, operation),
        source_revision=expected + 1,
        author_id="support",
        created_at="2026-10-25T01:00:00Z",
    )
    return request, manifest, target


def test_materialization_is_atomic_retained_and_durable(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    request, manifest, target = candidate()
    repo = SQLiteProjectRepository(path)
    assert isinstance(repo, MaterializationRepository)
    record = repo.commit_materialization(SCOPE, "stage", request, manifest, target)
    assert record.receipt.source_revision == manifest.source_revision
    assert record.receipt.revision == 1
    assert repo.revision(SCOPE, "hr").content_json == target.content_json
    assert repo.project(SCOPE, "hr").head_revision == 1
    with pytest.raises(VersioningError, match="retained"):
        repo.delete_release(SCOPE, "hr", target.release_id)
    repo.close()
    reopened = SQLiteProjectRepository(path)
    try:
        assert reopened.materialization_receipt(SCOPE, request.operation_id) == record
        # A retry preserves the original trusted actor/time, independent of candidate data.
        assert (
            reopened.commit_materialization(
                SCOPE, "stage", request, manifest, replace(target, author_id="other")
            )
            == record
        )
        assert (
            reopened.materialization_receipt(
                Scope(organization_id="acme", environment_id="prod"), request.operation_id
            )
            is None
        )
    finally:
        reopened.close()


def test_existing_project_name_and_revision_chain_are_preserved(tmp_path: Path) -> None:
    repo = SQLiteProjectRepository(tmp_path / "projects.db")
    try:
        request, manifest, target = candidate(expected=1)
        repo.create(SCOPE, "hr", "Human Resources", "{}", "Initial", "author")
        repo.commit_materialization(SCOPE, "stage", request, manifest, target)
        assert repo.project(SCOPE, "hr").name == "Human Resources"
        assert repo.project(SCOPE, "hr").head_revision == 2
        assert repo.revision(SCOPE, "hr").parent_revision == 1
        assert len(repo.history(SCOPE, "hr")) == 2
    finally:
        repo.close()


@pytest.mark.parametrize("change", ["request", "stage", "digest"])
def test_operation_reuse_rejects_changed_identity(tmp_path: Path, change: str) -> None:
    repo = SQLiteProjectRepository(tmp_path / "projects.db")
    try:
        request, manifest, target = candidate()
        repo.commit_materialization(SCOPE, "stage", request, manifest, target)
        changed_request = (
            request.model_copy(update={"configuration_revision": "config-2"})
            if change == "request"
            else request
        )
        changed_manifest = (
            manifest.model_copy(update={"package_digest": "sha256:" + "a" * 64})
            if change == "digest"
            else manifest
        )
        with pytest.raises(ActivationError) as failure:
            repo.commit_materialization(
                SCOPE,
                "other" if change == "stage" else "stage",
                changed_request,
                changed_manifest,
                target,
            )
        assert failure.value.code == "conflict"
        assert repo.project(SCOPE, "hr").head_revision == 1
    finally:
        repo.close()


def test_compare_and_swap_has_one_winner_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    one, two = SQLiteProjectRepository(path), SQLiteProjectRepository(path)

    def commit(repo: SQLiteProjectRepository, operation: str) -> str:
        request, manifest, target = candidate(operation)
        try:
            repo.commit_materialization(SCOPE, "stage", request, manifest, target)
            return "committed"
        except ActivationError as error:
            return error.code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: commit(*item), [(one, "one"), (two, "two")]))
        assert sorted(results) == ["committed", "conflict"]
        assert len(one.history(SCOPE, "hr")) == 1
    finally:
        one.close()
        two.close()


def test_late_storage_failure_rolls_back_every_write(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    repo = SQLiteProjectRepository(path)
    request, manifest, target = candidate()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER fail_receipt BEFORE INSERT ON package_materializations BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            repo.commit_materialization(SCOPE, "stage", request, manifest, target)
        assert repo.list_projects(SCOPE) == ()
        assert repo.materialization_receipt(SCOPE, request.operation_id) is None
        with sqlite3.connect(path) as db:
            for table in (
                "revisions",
                "releases",
                "release_references",
                "package_materializations",
            ):
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        repo.close()


def test_scope_or_candidate_mismatch_has_no_writes(tmp_path: Path) -> None:
    repo = SQLiteProjectRepository(tmp_path / "projects.db")
    request, manifest, target = candidate()
    try:
        for invalid in (
            replace(target, release_id="wrong"),
            replace(target, source_revision=2),
            replace(target, scope=Scope(organization_id="other", environment_id="uat")),
            replace(target, executable_digest="sha256:" + "f" * 64),
        ):
            with pytest.raises(ActivationError) as failure:
                repo.commit_materialization(SCOPE, "stage", request, manifest, invalid)
            assert failure.value.code == "invalid_release"
        assert repo.list_projects(SCOPE) == ()
    finally:
        repo.close()


def test_concurrent_duplicate_operations_return_same_original_receipt(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    one, two = SQLiteProjectRepository(path), SQLiteProjectRepository(path)
    request, manifest, target = candidate()

    def commit(repo: SQLiteProjectRepository):
        return repo.commit_materialization(SCOPE, "stage", request, manifest, target)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(executor.map(commit, (one, two)))
        assert records[0] == records[1]
        assert len(one.history(SCOPE, "hr")) == 1
    finally:
        one.close()
        two.close()


def test_missing_project_requires_zero_expected_revision(tmp_path: Path) -> None:
    repo = SQLiteProjectRepository(tmp_path / "projects.db")
    try:
        request, manifest, target = candidate(expected=1)
        with pytest.raises(ActivationError) as failure:
            repo.commit_materialization(SCOPE, "stage", request, manifest, target)
        assert failure.value.code == "conflict"
        assert repo.list_projects(SCOPE) == ()
    finally:
        repo.close()
