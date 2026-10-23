"""Independent persistence, concurrency and isolation checks for project storage."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.types import VersioningError

SCOPE = Scope(organization_id="acme", environment_id="dev")


def seed(repo: SQLiteProjectRepository) -> None:
    repo.create(SCOPE, "onboarding", "Onboarding", '{"revision":1}', "Initial", "author")


def publish(repo: SQLiteProjectRepository, revision: int = 1, key: str = "request-1"):
    return repo.publish(
        SCOPE, "onboarding", revision, key, "[]", "digest", "executable", "publisher"
    )


def test_immutable_revision_chain_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    repo = SQLiteProjectRepository(path)
    seed(repo)
    second = repo.save(SCOPE, "onboarding", 1, '{"revision":2}', "Edited", "editor")
    repo.close()
    reopened = SQLiteProjectRepository(path)
    assert reopened.project(SCOPE, "onboarding").head_revision == 2
    assert reopened.revision(SCOPE, "onboarding") == second
    first, saved = reopened.history(SCOPE, "onboarding")
    assert first.parent_revision is None
    assert saved.parent_revision == 1
    assert first.content_json == '{"revision":1}'
    assert saved.author_id == "editor"
    reopened.close()


def test_duplicate_create_and_stale_save_do_not_mutate_history() -> None:
    repo = SQLiteProjectRepository(":memory:")
    seed(repo)
    with pytest.raises(VersioningError, match="already exists") as error:
        seed(repo)
    assert error.value.code == "conflict"
    repo.save(SCOPE, "onboarding", 1, "{}", "Edit", "editor")
    with pytest.raises(VersioningError) as error:
        repo.save(SCOPE, "onboarding", 1, "stale", "Lost update", "other")
    assert error.value.code == "conflict"
    assert len(repo.history(SCOPE, "onboarding")) == 2
    assert repo.revision(SCOPE, "onboarding").content_json == "{}"


def test_independent_connections_serialize_competing_edits(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    first, second = SQLiteProjectRepository(path), SQLiteProjectRepository(path)
    seed(first)

    def edit(repo: SQLiteProjectRepository) -> str:
        try:
            repo.save(SCOPE, "onboarding", 1, "{}", "Concurrent edit", "editor")
            return "saved"
        except VersioningError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, (first, second))) == ["conflict", "saved"]
    assert len(first.history(SCOPE, "onboarding")) == 2


def test_publication_retries_return_original_immutable_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    repo = SQLiteProjectRepository(path)
    seed(repo)
    release = publish(repo)
    repo.save(SCOPE, "onboarding", 1, '{"revision":2}', "Edit", "editor")
    assert publish(repo) == release
    assert release.source_revision == 1
    assert release.content_json == '{"revision":1}'
    with pytest.raises(VersioningError) as error:
        publish(repo, revision=2)
    assert error.value.code == "conflict"
    repo.close()
    reopened = SQLiteProjectRepository(path)
    assert reopened.published(SCOPE, "onboarding", "request-1") == release


def test_competing_publications_share_single_release(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    first, second = SQLiteProjectRepository(path), SQLiteProjectRepository(path)
    seed(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        releases = list(pool.map(publish, (first, second)))
    assert releases[0] == releases[1]


@pytest.mark.parametrize(
    "foreign",
    [
        Scope(organization_id="other", environment_id="dev"),
        Scope(organization_id="acme", environment_id="uat"),
    ],
)
def test_all_resource_access_is_scoped(foreign: Scope) -> None:
    repo = SQLiteProjectRepository(":memory:")
    seed(repo)
    release = publish(repo)
    operations = [
        lambda: repo.project(foreign, "onboarding"),
        lambda: repo.revision(foreign, "onboarding", 1),
        lambda: repo.history(foreign, "onboarding"),
        lambda: repo.save(foreign, "onboarding", 1, "{}", "Edit", "editor"),
        lambda: repo.published(foreign, "onboarding", "request-1"),
        lambda: repo.publish(foreign, "onboarding", 1, "request", "[]", "digest", "exec", "actor"),
        lambda: repo.release(foreign, "onboarding", release.release_id),
        lambda: repo.retain(foreign, "onboarding", release.release_id, "run"),
        lambda: repo.release_reference(foreign, "onboarding", release.release_id, "run"),
        lambda: repo.delete_release(foreign, "onboarding", release.release_id),
    ]
    for operation in operations:
        with pytest.raises(VersioningError) as error:
            operation()
        assert error.value.code == "not_found"
    repo.create(foreign, "onboarding", "Independent", "{}", "Initial", "other")
    assert repo.project(SCOPE, "onboarding").name == "Onboarding"


def test_references_prevent_deletion_until_each_is_released() -> None:
    repo = SQLiteProjectRepository(":memory:")
    seed(repo)
    release = publish(repo)
    for reference in ("deployment", "run", "run"):
        repo.retain(SCOPE, "onboarding", release.release_id, reference)
    for reference in ("deployment", "run"):
        with pytest.raises(VersioningError) as error:
            repo.delete_release(SCOPE, "onboarding", release.release_id)
        assert error.value.code == "referenced"
        repo.release_reference(SCOPE, "onboarding", release.release_id, reference)
    repo.delete_release(SCOPE, "onboarding", release.release_id)
    with pytest.raises(VersioningError) as error:
        repo.release(SCOPE, "onboarding", release.release_id)
    assert error.value.code == "not_found"
    assert repo.revision(SCOPE, "onboarding", 1).number == 1


def test_missing_source_does_not_create_publication() -> None:
    repo = SQLiteProjectRepository(":memory:")
    seed(repo)
    with pytest.raises(VersioningError):
        publish(repo, revision=99)
    assert repo.published(SCOPE, "onboarding", "request-1") is None


def test_deleted_publication_key_cannot_be_reused_after_reopening(tmp_path: Path) -> None:
    path = tmp_path / "projects.db"
    repo = SQLiteProjectRepository(path)
    seed(repo)
    release = publish(repo)
    repo.delete_release(SCOPE, "onboarding", release.release_id)
    repo.close()
    reopened = SQLiteProjectRepository(path)
    with pytest.raises(VersioningError) as error:
        publish(reopened)
    assert error.value.code == "conflict"
    with pytest.raises(VersioningError):
        reopened.published(SCOPE, "onboarding", "request-1")
    assert publish(reopened, key="new-request").release_id != release.release_id
