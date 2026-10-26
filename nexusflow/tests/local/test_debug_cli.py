"""Local debugger CLI opt-in and preflight behavior without owned server startup."""

import asyncio
import json
from unittest.mock import Mock

import pytest

from nexusflow.apps import local
from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_debug import enable_debugger


def test_cli_enable_is_explicit_idempotent_and_metadata_only(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    key = (tmp_path / "private-key.pem").read_bytes()
    assert local.main(["enable-debugger", "--state-dir", str(tmp_path)]) == 0
    first = json.loads(capsys.readouterr().out)
    marker = (tmp_path / "debugger.json").read_bytes()
    assert first == {
        "profile": "local-demo",
        "debugger": "enabled",
        "organization_id": "local-demo",
        "environment_id": "dev",
    }
    assert local.main(["enable-debugger", "--state-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert (tmp_path / "debugger.json").read_bytes() == marker
    assert (tmp_path / "private-key.pem").read_bytes() == key


def test_cli_enable_respects_installation_lock(tmp_path, capsys):
    initialize(tmp_path)
    with local.installation_lock(tmp_path):
        assert local.main(["enable-debugger", "--state-dir", str(tmp_path)]) == 2
    assert not (tmp_path / "debugger.json").exists()
    assert "already in use" in capsys.readouterr().err


def test_cli_profile_error_is_sanitized(tmp_path, monkeypatch, capsys):
    initialize(tmp_path)
    monkeypatch.setattr(
        local, "enable_debugger", Mock(side_effect=RuntimeError("password=sentinel"))
    )
    assert local.main(["enable-debugger", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "sentinel" not in output.out + output.err


def test_invalid_enabled_profile_fails_before_runtime_or_listen(tmp_path, monkeypatch):
    initialize(tmp_path)
    enable_debugger(tmp_path)
    (tmp_path / "debug-sessions.db").unlink()
    runtime = Mock(side_effect=AssertionError("Must not start Temporal"))
    listener = Mock(side_effect=AssertionError("Must not bind API"))
    # asyncio creates its own socketpair; construct the loop before patching socket.
    with asyncio.Runner() as runner:
        runner.get_loop()
        monkeypatch.setattr(local, "local_runtime", runtime)
        monkeypatch.setattr(local.socket, "socket", listener)
        with pytest.raises(ValueError):
            runner.run(local.serve_local(tmp_path))
    runtime.assert_not_called()
    listener.assert_not_called()
    assert not (tmp_path / "debug-sessions.db").exists()
