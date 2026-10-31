"""Explicit fixture enablement preserves revoked identity and incomplete recovery state."""

import json
import sqlite3
from unittest.mock import Mock

import pytest

from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID, initialize
from nexusflow.apps.local_mocks import enable_mock_fixtures, load_mock_fixture_profile
from nexusflow.contracts.models import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant, IdentityError

MARKER = "http-mock-fixtures.json"
DATABASE = "http-mock-fixtures.db"
GRANT = "local-mock-fixture-admin"
SCOPE = Scope(organization_id="local-demo", environment_id="dev")


def files(directory, *, data_only=False):
    # Read-only WAL inspection may create bookkeeping sidecars, never fixture
    # data/schema repairs. Orphan-marker tests still compare every file.
    return {
        p.name: p.read_bytes()
        for p in directory.iterdir()
        if p.is_file() and (not data_only or p.name not in {DATABASE + "-wal", DATABASE + "-shm"})
    }


def test_disabled_profile_does_not_create_or_modify_state(tmp_path):
    settings = initialize(tmp_path)
    before = files(tmp_path)
    assert load_mock_fixture_profile(tmp_path, settings) is None
    assert files(tmp_path) == before


def test_enable_and_retry_are_scoped_and_preserve_revoked_admin(tmp_path, monkeypatch):
    settings = initialize(tmp_path)
    before = files(tmp_path)
    profile = enable_mock_fixtures(tmp_path)
    assert profile.scope == SCOPE and profile.installation_id == settings.installation_id
    assert profile.fixtures_database == DATABASE
    assert profile.profile == "local-demo-http-mock-fixtures"
    for name, content in before.items():
        if name != "identity.db":
            assert (tmp_path / name).read_bytes() == content
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        admin = next(g for g in identity.grants(SCOPE) if g.grant_id == GRANT)
        assert admin == Grant(
            GRANT, SCOPE, TENANT_ID, "user", USER_ID, "organization_administrator"
        )
        assert all(
            g.grant_id != GRANT
            for g in identity.grants(SCOPE.model_copy(update={"environment_id": "prod"}))
        )
        identity.revoke_grant(SCOPE, GRANT, expected_revision=1)
    finally:
        identity.close()
    before_retry = files(tmp_path, data_only=True)
    unexpected = Mock(side_effect=AssertionError("retry must not create repositories"))
    monkeypatch.setattr("nexusflow.apps.local_mocks.SQLiteIdentityRepository", unexpected)
    monkeypatch.setattr("nexusflow.apps.local_mocks.SQLiteHTTPMockScenarios", unexpected)
    assert enable_mock_fixtures(tmp_path) == profile
    assert load_mock_fixture_profile(tmp_path, settings) == profile
    assert files(tmp_path, data_only=True) == before_retry
    unexpected.assert_not_called()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_orphan_database_or_sidecar_is_never_adopted(tmp_path, suffix):
    settings = initialize(tmp_path)
    orphan = tmp_path / (DATABASE + suffix)
    orphan.write_bytes(b"private preserved orphan")
    before = files(tmp_path)
    for operation in (
        lambda: load_mock_fixture_profile(tmp_path, settings),
        lambda: enable_mock_fixtures(tmp_path),
    ):
        with pytest.raises(ValueError, match="Incomplete"):
            operation()
    assert files(tmp_path) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "installation",
        "noncanonical_uuid",
        "organization",
        "uat",
        "prod",
        "filename",
        "unknown",
        "missing",
        "schema_version",
        "profile",
        "duplicate",
        "nonfinite",
        "oversize",
        "list",
        "invalid_json",
    ],
)
def test_invalid_marker_rejected_before_database_access(tmp_path, monkeypatch, mutation):
    settings = initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    marker = tmp_path / MARKER
    document = json.loads(marker.read_text())
    if mutation == "installation":
        document["installation_id"] = "00000000-0000-4000-8000-000000000000"
    elif mutation == "noncanonical_uuid":
        document["installation_id"] = document["installation_id"].replace("-", "")
    elif mutation == "organization":
        document["scope"]["organization_id"] = "other"
    elif mutation in {"uat", "prod"}:
        document["scope"]["environment_id"] = mutation
    elif mutation == "filename":
        document["fixtures_database"] = "../private.db"
    elif mutation == "unknown":
        document["private"] = "not allowed"
    elif mutation == "missing":
        document.pop("profile")
    elif mutation in {"schema_version", "profile"}:
        document[mutation] = "unknown"
    encoded = json.dumps(document)
    if mutation == "duplicate":
        encoded = encoded[:-1] + ',"scope":' + json.dumps(document["scope"]) + "}"
    elif mutation == "nonfinite":
        encoded = encoded[:-1] + ',"unknown":NaN}'
    elif mutation == "oversize":
        encoded = " " * 16385
    elif mutation == "list":
        encoded = "[]"
    elif mutation == "invalid_json":
        encoded = "private invalid JSON"
    marker.write_text(encoded)
    before = files(tmp_path)
    connect = Mock(side_effect=AssertionError("must not inspect database"))
    monkeypatch.setattr("nexusflow.apps.local_mocks.sqlite3.connect", connect)
    with pytest.raises(ValueError, match="Invalid local mock fixture profile"):
        load_mock_fixture_profile(tmp_path, settings)
    assert files(tmp_path) == before
    connect.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "invalid",
        "empty",
        "view",
        "missing_column",
        "wrong_pk",
        "wrong_type",
        "extra_column",
    ],
)
def test_database_recovery_rejected_without_repair(tmp_path, mutation):
    settings = initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    database = tmp_path / DATABASE
    if mutation == "missing":
        database.unlink()
    elif mutation == "invalid":
        database.write_bytes(b"private invalid database")
    else:
        with sqlite3.connect(database) as db:
            db.execute("DROP TABLE http_mock_scenarios")
            if mutation == "view":
                db.execute("CREATE VIEW http_mock_scenarios AS SELECT 1 AS organization_id")
            elif mutation == "missing_column":
                db.execute("CREATE TABLE http_mock_scenarios(organization_id TEXT)")
            elif mutation in {"wrong_pk", "wrong_type", "extra_column"}:
                body_type = "INTEGER" if mutation == "wrong_type" else "TEXT"
                pk = (
                    ""
                    if mutation == "wrong_pk"
                    else ", PRIMARY KEY(organization_id,environment_id,connection_id,connection_revision)"
                )
                db.execute(
                    f"CREATE TABLE http_mock_scenarios(organization_id TEXT NOT NULL, environment_id TEXT NOT NULL, connection_id TEXT NOT NULL, connection_revision TEXT NOT NULL, fixture_digest TEXT NOT NULL, stored_bytes INTEGER NOT NULL, definition_json {body_type} NOT NULL{pk})"
                )
                if mutation == "extra_column":
                    db.execute("ALTER TABLE http_mock_scenarios ADD COLUMN incompatible TEXT")
        db.close()
    before = files(tmp_path, data_only=True)
    for operation in (
        lambda: load_mock_fixture_profile(tmp_path, settings),
        lambda: enable_mock_fixtures(tmp_path),
    ):
        with pytest.raises(ValueError):
            operation()
    assert files(tmp_path, data_only=True) == before


def test_loader_inspects_only_schema_and_preserves_closed_state(tmp_path, monkeypatch):
    settings = initialize(tmp_path)
    profile = enable_mock_fixtures(tmp_path)
    with sqlite3.connect(tmp_path / DATABASE) as db:
        db.execute(
            "INSERT INTO http_mock_scenarios VALUES(?,?,?,?,?,?,?)",
            ("local-demo", "dev", "private", "v1", "bad", 0, "not fixture JSON"),
        )
    db.close()
    before = files(tmp_path, data_only=True)
    original = sqlite3.connect
    queries = []

    def readonly(path, **kwargs):
        assert path.endswith("?mode=ro") and kwargs == {"uri": True}
        connection = original(path, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr("nexusflow.apps.local_mocks.sqlite3.connect", readonly)
    assert load_mock_fixture_profile(tmp_path, settings) == profile
    assert all("definition_json" not in query for query in queries)
    assert files(tmp_path, data_only=True) == before


def test_loader_does_not_ignore_committed_wal_schema_changes(tmp_path):
    settings = initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    database = tmp_path / DATABASE
    writer = sqlite3.connect(database)
    try:
        writer.execute("DROP TABLE http_mock_scenarios")
        writer.commit()
        assert (tmp_path / (DATABASE + "-wal")).exists()
        before = {name: (tmp_path / name).read_bytes() for name in (DATABASE, DATABASE + "-wal")}
        with pytest.raises(ValueError, match="database"):
            load_mock_fixture_profile(tmp_path, settings)
        assert {name: (tmp_path / name).read_bytes() for name in before} == before
    finally:
        writer.close()


def test_existing_grant_tombstone_is_not_restored(tmp_path):
    initialize(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.save_grant(
            Grant(GRANT, SCOPE, TENANT_ID, "user", USER_ID, "organization_administrator"),
            expected_revision=0,
        )
        identity.revoke_grant(SCOPE, GRANT, expected_revision=1)
    finally:
        identity.close()
    before = files(tmp_path)
    with pytest.raises(IdentityError):
        enable_mock_fixtures(tmp_path)
    assert files(tmp_path) == before


def test_enable_never_recreates_membership_or_author_grant(tmp_path):
    initialize(tmp_path)
    identity = SQLiteIdentityRepository(tmp_path / "identity.db")
    try:
        identity.revoke_grant(SCOPE, "local-author", expected_revision=1)
        identity.remove_membership("local-demo", TENANT_ID, "user", USER_ID)
    finally:
        identity.close()
    before = files(tmp_path)
    with pytest.raises(IdentityError):
        enable_mock_fixtures(tmp_path)
    assert files(tmp_path) == before


def test_partial_database_failure_cannot_be_silently_retried(tmp_path, monkeypatch):
    initialize(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(
            "nexusflow.apps.local_mocks.SQLiteHTTPMockScenarios",
            Mock(side_effect=OSError("private failure")),
        )
        with pytest.raises(OSError):
            enable_mock_fixtures(tmp_path)
    assert not (tmp_path / MARKER).exists() and not (tmp_path / DATABASE).exists()
    with pytest.raises(IdentityError):
        enable_mock_fixtures(tmp_path)
    assert not (tmp_path / MARKER).exists() and not (tmp_path / DATABASE).exists()


@pytest.mark.parametrize("name", [MARKER, DATABASE, DATABASE + "-wal", DATABASE + "-shm"])
def test_symlink_paths_are_not_followed(tmp_path, name):
    settings = initialize(tmp_path)
    target = tmp_path.parent / ("external-" + name)
    target.write_bytes(b"private external state")
    try:
        (tmp_path / name).symlink_to(target)
    except OSError:
        pytest.skip("Host does not permit unprivileged symlink creation")
    with pytest.raises(ValueError, match="paths"):
        load_mock_fixture_profile(tmp_path, settings)
    assert target.read_bytes() == b"private external state"
