"""Bounded version browsing stays scoped and never fetches historical snapshots."""

import json
import sqlite3

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.project_versioning.canonical import canonical, compare_json
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import VersioningError
from nexusflow.tests.project_versioning.test_service import Authorization, Catalog, Validator

SCOPE = Scope(organization_id="acme", environment_id="dev")


@pytest.fixture
def system():
    repo = SQLiteProjectRepository(":memory:")
    auth = Authorization()
    service = ProjectService(repo, auth, Validator(), Catalog())
    repo.create(SCOPE, "hr", "HR", '{"schema_version":"0.1"}', "First", "author")
    yield service, repo, auth
    repo.close()


def test_metadata_query_never_reads_snapshot_and_cursor_stays_stable(system):
    service, repo, _ = system
    for revision in range(1, 5):
        repo.save(SCOPE, "hr", revision, "{}", "Saved", "author")

    def deny_content(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table == "revisions" and column == "content":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    repo._db.set_authorizer(deny_content)
    first = service.history_page(SCOPE, "hr", limit=2)
    assert [item.number for item in first.revisions] == [5, 4]
    assert first.next_before_revision == 4
    assert first.revisions[0].project_id == "hr"
    assert first.revisions[0].parent_revision == 4
    repo._db.set_authorizer(None)
    repo.save(SCOPE, "hr", 5, "{}", "Concurrent save", "author")
    repo._db.set_authorizer(deny_content)
    second = service.history_page(SCOPE, "hr", limit=2, before_revision=4)
    assert [item.number for item in second.revisions] == [3, 2]
    last = service.history_page(SCOPE, "hr", limit=2, before_revision=2)
    assert [item.number for item in last.revisions] == [1]
    assert last.next_before_revision is None
    assert service.history_page(SCOPE, "hr", before_revision=1).revisions == ()
    repo._db.set_authorizer(None)


@pytest.mark.parametrize("limit", [True, 0, 101, 1.0, "1"])
def test_invalid_history_limit(system, limit):
    service, _, _ = system
    with pytest.raises(VersioningError) as error:
        service.history_page(SCOPE, "hr", limit=limit)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("number", [True, 0, -1, 2**63, 1.0, "1"])
def test_invalid_history_cursor_and_compare_revisions(system, number):
    service, _, _ = system
    for call in (
        lambda: service.history_page(SCOPE, "hr", before_revision=number),
        lambda: service.compare_bounded(SCOPE, "hr", number, 1),
        lambda: service.compare_bounded(SCOPE, "hr", 1, number),
    ):
        with pytest.raises(VersioningError) as error:
            call()
        assert error.value.code == "invalid_request"


@pytest.mark.parametrize(
    "scope,project",
    [
        (Scope(organization_id="foreign", environment_id="dev"), "hr"),
        (Scope(organization_id="acme", environment_id="uat"), "hr"),
        (SCOPE, "missing"),
    ],
)
def test_history_and_compare_do_not_cross_scope(system, scope, project):
    service, _, _ = system
    for call in (
        lambda: service.history_page(scope, project),
        lambda: service.compare_bounded(scope, project, 1, 1),
    ):
        with pytest.raises(VersioningError) as error:
            call()
        assert error.value.code == "not_found"


def test_revoked_permission_is_rechecked_before_read(system):
    service, _, auth = system
    service.history_page(SCOPE, "hr")
    service.compare_bounded(SCOPE, "hr", 1, 1)
    auth.denied = True
    for call in (
        lambda: service.history_page(SCOPE, "hr"),
        lambda: service.compare_bounded(SCOPE, "hr", 1, 1),
    ):
        with pytest.raises(VersioningError) as error:
            call()
        assert error.value.code == "forbidden"
    assert all(permission == "project.read" for _, permission, _ in auth.calls)


def test_compare_keeps_json_null_distinct_from_missing_and_escapes_pointer():
    changes = compare_json(
        '{"a/b":null,"layout":{"x":1}}',
        '{"til~de":null,"layout":{"x":2}}',
        max_changes=1000,
        max_bytes=262144,
    )
    assert [(c.path, c.before_json, c.after_json) for c in changes] == [
        ("/a~1b", "null", None),
        ("/til~0de", None, "null"),
    ]
    assert len(compare_json('{"layout":{}}', '{"layout":{"x":1}}', True, max_changes=1000)) == 1


def test_change_count_budget_aborts_before_serializing_later_values(monkeypatch):
    import nexusflow.modules.project_versioning.canonical as module

    original = module.canonical

    def guarded(value):
        if value == "must-not-visit":
            raise AssertionError("comparison continued after exceeding its limit")
        return original(value)

    monkeypatch.setattr(module, "canonical", guarded)
    with pytest.raises(VersioningError) as error:
        compare_json("{}", '{"a":1,"b":2,"c":"must-not-visit"}', max_changes=1)
    assert error.value.code == "comparison_limit"


def test_byte_budget_counts_utf8_encoded_change_objects():
    change = compare_json("{}", '{"a":"é"}')[0]
    size = len(
        canonical(
            {
                "path": change.path,
                "before_json": change.before_json,
                "after_json": change.after_json,
            }
        ).encode("utf-8")
    )
    assert compare_json("{}", '{"a":"é"}', max_bytes=size) == (change,)
    with pytest.raises(VersioningError) as error:
        compare_json("{}", '{"a":"é"}', max_bytes=size - 1)
    assert error.value.code == "comparison_limit"


def test_bounded_service_limits_do_not_change_legacy_compare(system):
    service, repo, _ = system
    before = repo.save(SCOPE, "hr", 1, "{}", "Empty", "author")
    large = json.dumps({str(number): number for number in range(1001)})
    after = repo.save(SCOPE, "hr", 2, large, "Many changes", "author")
    assert len(service.compare(SCOPE, "hr", before.number, after.number)) == 1001
    with pytest.raises(VersioningError) as error:
        service.compare_bounded(SCOPE, "hr", before.number, after.number)
    assert error.value.code == "comparison_limit"
    after = repo.save(SCOPE, "hr", 3, json.dumps({"large": "x" * 262144}), "Large value", "author")
    with pytest.raises(VersioningError) as error:
        service.compare_bounded(SCOPE, "hr", before.number, after.number)
    assert error.value.code == "comparison_limit"


@pytest.mark.parametrize("include_layout", [0, 1, "true", None])
def test_bounded_compare_requires_actual_boolean(system, include_layout):
    service, _, _ = system
    with pytest.raises(VersioningError) as error:
        service.compare_bounded(SCOPE, "hr", 1, 1, include_layout)
    assert error.value.code == "invalid_request"


def test_count_limit_accepts_exact_boundary_and_empty_comparison():
    payload = json.dumps({str(number): number for number in range(1000)})
    assert len(compare_json("{}", payload, max_changes=1000)) == 1000
    assert compare_json(payload, payload, max_changes=0, max_bytes=0) == ()


def test_page_metadata_survives_restart(tmp_path):
    database = tmp_path / "projects.db"
    with_repo = SQLiteProjectRepository(database)
    original = with_repo.create(SCOPE, "hr", "HR", "{}", "Initial", "author")
    with_repo.close()
    reopened = SQLiteProjectRepository(database)
    try:
        page = reopened.history_page(SCOPE, "hr", limit=100, before_revision=2**63 - 1)
        assert len(page.revisions) == 1
        item = page.revisions[0]
        assert (
            item.number,
            item.parent_revision,
            item.summary,
            item.author_id,
            item.created_at,
        ) == (
            original.number,
            original.parent_revision,
            original.summary,
            original.author_id,
            original.created_at,
        )
        assert page.next_before_revision is None
    finally:
        reopened.close()
