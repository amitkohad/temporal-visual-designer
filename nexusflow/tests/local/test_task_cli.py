"""Explicit feature command shares launcher locking and emits no credentials."""

import json

from nexusflow.apps import local


def test_enable_command_is_explicit_locked_and_idempotent(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert not (tmp_path / "human-tasks.json").exists()
    with local.installation_lock(tmp_path):
        assert local.main(["enable-tasks", "--state-dir", str(tmp_path)]) == 2
    denied = capsys.readouterr()
    assert "already in use" in denied.err
    assert not (tmp_path / "human-tasks.json").exists()
    assert local.main(["enable-tasks", "--state-dir", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "profile": "local-demo",
        "human_tasks": "enabled",
        "organization_id": "local-demo",
        "environment_id": "dev",
    }
    previous = (tmp_path / "human-tasks.json").read_bytes()
    assert local.main(["enable-tasks", "--state-dir", str(tmp_path)]) == 0
    assert (tmp_path / "human-tasks.json").read_bytes() == previous


def test_enable_command_redacts_provider_failure(tmp_path, monkeypatch, capsys):
    def broken(directory):
        raise RuntimeError("private-identity-provider-details")

    monkeypatch.setattr(local, "enable_tasks", broken)
    assert local.main(["enable-tasks", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "private-identity-provider-details" not in output.out + output.err
