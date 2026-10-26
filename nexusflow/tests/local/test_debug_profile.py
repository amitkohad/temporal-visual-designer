"""Profile enablement preserves identity and rejects incomplete debugger recovery state."""

import json
import sqlite3
from hashlib import sha256
from unittest.mock import Mock

import pytest

from nexusflow.apps.local_bootstrap import initialize, load_settings
from nexusflow.apps.local_debug import enable_debugger, load_debug_profile


def snapshots(directory):
    return {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in directory.iterdir()
        if path.is_file()
        and path.name != "debugger.json"
        and not path.name.startswith("debug-sessions.db")
    }


def test_optional_profile_does_not_create_state(tmp_path):
    settings = initialize(tmp_path)
    before = {path.name for path in tmp_path.iterdir()}
    assert load_debug_profile(tmp_path, settings) is None
    assert {path.name for path in tmp_path.iterdir()} == before


def test_enable_and_exact_retry_preserve_existing_installation(tmp_path, monkeypatch):
    settings = initialize(tmp_path)
    before = snapshots(tmp_path)
    profile = enable_debugger(tmp_path)
    assert profile.scope.environment_id == "dev"
    assert profile.scope.organization_id == settings.organization_id
    assert profile.installation_id == settings.installation_id
    assert snapshots(tmp_path) == before
    assert load_debug_profile(tmp_path, load_settings(tmp_path)) == profile
    saved = {
        name: (tmp_path / name).read_bytes() for name in ("debugger.json", "debug-sessions.db")
    }
    constructor = Mock(side_effect=AssertionError("must not open writable repository"))
    monkeypatch.setattr("nexusflow.apps.local_debug.SQLiteDebugSessionRepository", constructor)
    assert enable_debugger(tmp_path) == profile
    assert snapshots(tmp_path) == before
    assert {name: (tmp_path / name).read_bytes() for name in saved} == saved
    constructor.assert_not_called()


def test_missing_database_fails_without_recreation(tmp_path):
    settings = initialize(tmp_path)
    enable_debugger(tmp_path)
    (tmp_path / "debug-sessions.db").unlink()
    for operation in (
        lambda: load_debug_profile(tmp_path, settings),
        lambda: enable_debugger(tmp_path),
    ):
        with pytest.raises(ValueError, match="Missing"):
            operation()
        assert not (tmp_path / "debug-sessions.db").exists()


def test_orphan_database_is_not_adopted_or_modified(tmp_path):
    settings = initialize(tmp_path)
    (tmp_path / "debug-sessions.db").write_bytes(b"private orphan data")
    for operation in (
        lambda: load_debug_profile(tmp_path, settings),
        lambda: enable_debugger(tmp_path),
    ):
        with pytest.raises(ValueError, match="Incomplete"):
            operation()
    assert (tmp_path / "debug-sessions.db").read_bytes() == b"private orphan data"
    assert not (tmp_path / "debugger.json").exists()


@pytest.mark.parametrize(
    "mutation", ["foreign_installation", "foreign_organization", "uat", "filename", "unknown"]
)
def test_profile_tampering_fails_before_database_access(tmp_path, mutation, monkeypatch):
    settings = initialize(tmp_path)
    enable_debugger(tmp_path)
    marker = tmp_path / "debugger.json"
    data = json.loads(marker.read_text())
    if mutation == "foreign_installation":
        data["installation_id"] = "00000000-0000-4000-8000-000000000000"
    elif mutation == "foreign_organization":
        data["scope"]["organization_id"] = "foreign"
    elif mutation == "uat":
        data["scope"]["environment_id"] = "uat"
    elif mutation == "filename":
        data["sessions_database"] = "../outside.db"
    else:
        data["unexpected"] = True
    marker.write_text(json.dumps(data))
    constructor = Mock(side_effect=AssertionError("no database access"))
    monkeypatch.setattr("nexusflow.apps.local_debug.sqlite3.connect", constructor)
    with pytest.raises(ValueError):
        load_debug_profile(tmp_path, settings)
    constructor.assert_not_called()


@pytest.mark.parametrize("damage", ["missing_table", "missing_column", "corrupt"])
def test_invalid_database_is_preserved(tmp_path, damage):
    settings = initialize(tmp_path)
    enable_debugger(tmp_path)
    path = tmp_path / "debug-sessions.db"
    if damage == "corrupt":
        path.write_bytes(b"not SQLite")
    else:
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE debug_commands")
            if damage == "missing_column":
                db.execute("CREATE TABLE debug_commands (org TEXT, env TEXT)")
    before = path.read_bytes()
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        load_debug_profile(tmp_path, settings)
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", ["not json", " " * 16385])
def test_malformed_or_oversized_marker_does_not_create_database(tmp_path, payload):
    settings = initialize(tmp_path)
    (tmp_path / "debugger.json").write_text(payload)
    with pytest.raises(ValueError):
        load_debug_profile(tmp_path, settings)
    assert not (tmp_path / "debug-sessions.db").exists()


def test_failed_marker_publication_preserves_initialized_database(tmp_path, monkeypatch):
    initialize(tmp_path)
    from pathlib import Path

    original = Path.open

    def fail_marker(path, mode="r", *args, **kwargs):
        if path.name == "debugger.json" and mode == "x":
            raise OSError("publication failed")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_marker)
    with pytest.raises(OSError):
        enable_debugger(tmp_path)
    assert (tmp_path / "debug-sessions.db").is_file()
    assert not (tmp_path / "debugger.json").exists()
    with pytest.raises(ValueError, match="Incomplete"):
        enable_debugger(tmp_path)
