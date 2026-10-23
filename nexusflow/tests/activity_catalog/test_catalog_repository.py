"""Durability, concurrency and reference protection of local catalog metadata."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog_repository import SQLiteCatalogRepository
from nexusflow.modules.activity_catalog.repository import SQLiteIntakeRepository
from nexusflow.modules.activity_catalog.types import CatalogError
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.tests.activity_catalog.test_intake import SCOPE, source_bundle


def setup_catalog(path: Path):
    archive, digest = source_bundle()
    intake = SQLiteIntakeRepository(path)
    imported = intake.put(SCOPE, validate_bundle(archive), "importer")
    intake.close()
    catalog = SQLiteCatalogRepository(path)
    return catalog, imported, archive, digest


def test_reopen_idempotency_and_scope(tmp_path):
    path = tmp_path / "catalog.db"
    catalog, imported, archive, digest = setup_catalog(path)
    entry = catalog.publish(imported, archive, "publisher")
    assert catalog.publish(imported, b"ignored retry", "other") == entry
    assert catalog.export(SCOPE, entry.reference).archive == archive
    catalog.close()
    catalog = SQLiteCatalogRepository(path)
    assert catalog.get(SCOPE, entry.reference) == entry
    assert catalog.by_digest(Scope(organization_id="other", environment_id="dev"), digest) is None
    with pytest.raises(CatalogError, match="not found"):
        catalog.get(Scope(organization_id="acme", environment_id="uat"), entry.reference)
    catalog.close()


def test_lifecycle_hold_export_and_tombstone(tmp_path):
    catalog, imported, archive, digest = setup_catalog(tmp_path / "catalog.db")
    entry = catalog.publish(imported, archive, "publisher")
    ref = entry.reference
    catalog.retain(SCOPE, ref, "release-1")
    deprecated = catalog.transition(SCOPE, ref, "deprecated", 1, "support")
    assert catalog.search(SCOPE) == ()
    assert catalog.search(SCOPE, include_deprecated=True) == (deprecated,)
    assert catalog.search(SCOPE, query="%", include_deprecated=True) == ()
    withdrawn = catalog.transition(SCOPE, ref, "withdrawn", 2, "support")
    assert catalog.transition(SCOPE, ref, "withdrawn", 1, "support") == withdrawn
    assert catalog.search(SCOPE, include_deprecated=True) == ()
    assert catalog.export(SCOPE, ref, "release-1").archive == archive
    catalog.retain(SCOPE, ref, "release-1")
    for operation, code in [
        (lambda: catalog.retain(SCOPE, ref, "release-2"), "withdrawn"),
        (lambda: catalog.export(SCOPE, ref), "withdrawn"),
        (lambda: catalog.export(SCOPE, ref, "foreign"), "forbidden"),
        (lambda: catalog.delete(SCOPE, ref, "support"), "referenced"),
        (lambda: catalog.transition(SCOPE, ref, "deprecated", 3, "support"), "invalid_transition"),
    ]:
        with pytest.raises(CatalogError) as error:
            operation()
        assert error.value.code == code
    catalog.release_reference(SCOPE, ref, "release-1")
    catalog.release_reference(SCOPE, ref, "release-1")
    catalog.delete(SCOPE, ref, "support")
    assert catalog.by_digest(SCOPE, digest) is None
    with pytest.raises(CatalogError) as error:
        catalog.publish(imported, archive, "publisher")
    assert error.value.code == "conflict"
    assert [event.action for event in catalog.events(SCOPE, ref)] == [
        "published",
        "deprecated",
        "withdrawn",
        "deleted",
    ]
    catalog.close()


def test_cross_connection_publication_and_cas(tmp_path):
    path = tmp_path / "catalog.db"
    first, imported, archive, _ = setup_catalog(path)
    second = SQLiteCatalogRepository(path)
    with ThreadPoolExecutor(2) as executor:
        results = list(
            executor.map(
                lambda repository: repository.publish(imported, archive, "publisher"),
                [first, second],
            )
        )
    assert results[0] == results[1]
    ref = results[0].reference
    first.transition(SCOPE, ref, "deprecated", 1, "publisher")
    with pytest.raises(CatalogError) as error:
        second.transition(SCOPE, ref, "withdrawn", 1, "publisher")
    assert error.value.code == "conflict"
    assert len(first.events(SCOPE, ref)) == 2
    first.close()
    second.close()


def test_event_failure_rolls_back_state(tmp_path, monkeypatch):
    catalog, imported, archive, digest = setup_catalog(tmp_path / "catalog.db")
    original = catalog._event

    def fail(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(catalog, "_event", fail)
    with pytest.raises(RuntimeError):
        catalog.publish(imported, archive, "publisher")
    assert catalog.by_digest(SCOPE, digest) is None
    monkeypatch.setattr(catalog, "_event", original)
    entry = catalog.publish(imported, archive, "publisher")
    monkeypatch.setattr(catalog, "_event", fail)
    with pytest.raises(RuntimeError):
        catalog.transition(SCOPE, entry.reference, "withdrawn", 1, "publisher")
    assert catalog.get(SCOPE, entry.reference) == entry
    assert len(catalog.events(SCOPE, entry.reference)) == 1
    catalog.close()


def test_retain_races_withdrawal_and_delete_across_connections(tmp_path):
    path = tmp_path / "catalog.db"
    first, imported, archive, _ = setup_catalog(path)
    second = SQLiteCatalogRepository(path)
    ref = first.publish(imported, archive, "publisher").reference

    def retain():
        try:
            second.retain(SCOPE, ref, "release")
            return "retained"
        except CatalogError as error:
            return error.code

    def withdraw_delete():
        first.transition(SCOPE, ref, "withdrawn", 1, "publisher")
        try:
            first.delete(SCOPE, ref, "publisher")
            return "deleted"
        except CatalogError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        hold = executor.submit(retain)
        deletion = executor.submit(withdraw_delete)
        results = hold.result(), deletion.result()
    assert results in {
        ("retained", "referenced"),
        ("withdrawn", "deleted"),
        ("not_found", "deleted"),
    }
    if results[0] == "retained":
        assert first.export(SCOPE, ref, "release").archive == archive
    first.close()
    second.close()


def test_metadata_queries_never_read_archives_and_filter_before_paging(tmp_path):
    import json
    import sqlite3
    from dataclasses import replace

    catalog, imported, archive, _ = setup_catalog(tmp_path / "catalog.db")
    entries = []
    for index, (name, category, state) in enumerate(
        [
            ("aaa", "other", "published"),
            ("bbb", "desired", "deprecated"),
            ("ccc", "desired", "withdrawn"),
            ("ddd", "desired", "published"),
            ("eee", "desired", "published"),
            ("fff", "desired", "published"),
        ]
    ):
        manifest = json.loads(imported.manifest_json)
        digest = "sha256:" + str(index) * 64
        manifest["descriptor"]["activity_ref"].update(activity_id=name, artifact_digest=digest)
        manifest["descriptor"].update(
            category=category, display_name="Straße %_", description="Find me"
        )
        entry = catalog.publish(
            replace(imported, digest=digest, manifest_json=json.dumps(manifest)),
            archive,
            "publisher",
        )
        if state != "published":
            entry = catalog.transition(SCOPE, entry.reference, state, 1, "publisher")
        entries.append(entry)

    def deny_archive(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table == "activity_catalog" and column == "archive":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    catalog._db.set_authorizer(deny_archive)
    assert catalog.get(SCOPE, entries[3].reference) == entries[3]
    assert catalog.by_digest(SCOPE, entries[3].reference.artifact_digest) == entries[3]
    assert catalog.search(SCOPE, query="STRASSE %_", category="desired", limit=1, offset=1) == (
        entries[4],
    )
    assert catalog.search(
        SCOPE, query="STRASSE %_", category="desired", include_deprecated=True, limit=1, offset=1
    ) == (entries[3],)
    assert catalog.search(SCOPE, query="%__", category="desired") == ()
    # Idempotent publication and lifecycle lookups also remain metadata-only.
    retry_manifest = replace(
        imported,
        digest=entries[3].reference.artifact_digest,
        manifest_json=entries[3].manifest_json,
    )
    assert catalog.publish(retry_manifest, archive, "other") == entries[3]
    catalog.retain(SCOPE, entries[3].reference, "usage")
    catalog.transition(SCOPE, entries[3].reference, "deprecated", 1, "publisher")
    with pytest.raises(sqlite3.DatabaseError):
        catalog.export(SCOPE, entries[3].reference)
    catalog._db.set_authorizer(None)
    assert catalog.export(SCOPE, entries[3].reference).archive == archive
    catalog.close()
