"""Independent launch preflight refuses lost or mismatched optional debugger state."""

import asyncio
import json

import pytest

from nexusflow.apps import local
from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_debug import enable_debugger


@pytest.mark.parametrize("damage", ["missing-database", "foreign-installation", "orphan-database"])
def test_debug_profile_damage_refuses_launch_before_runtime_writes(tmp_path, monkeypatch, damage):
    initialize(tmp_path)
    enable_debugger(tmp_path)
    marker = tmp_path / "debugger.json"
    database = tmp_path / "debug-sessions.db"
    if damage == "missing-database":
        database.unlink()
    elif damage == "foreign-installation":
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["installation_id"] = "00000000-0000-4000-8000-000000000000"
        marker.write_text(json.dumps(data), encoding="utf-8")
    else:
        marker.unlink()
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}

    def forbidden_runtime(*args, **kwargs):
        raise AssertionError("Invalid debugger state must fail before runtime startup")

    monkeypatch.setattr(local, "local_runtime", forbidden_runtime)
    with pytest.raises((ValueError, local.LocalCommandError)):
        asyncio.run(local.serve_local(tmp_path, api_port=18080, temporal_port=17233))
    assert not (tmp_path / "temporal.db").exists()
    assert not (tmp_path / "dispatch.db").exists()
    # The fixed launcher lock is the only allowed new file before refusing startup.
    after = {
        path.name: path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file() and path.name != ".launcher.lock"
    }
    assert after == before
