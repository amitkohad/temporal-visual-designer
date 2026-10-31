"""Explicit local fixture administration enablement and safe preflight."""

import asyncio
import json
from unittest.mock import Mock

import pytest

from nexusflow.apps import local
from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_mocks import enable_mock_fixtures


def test_cli_enable_is_explicit_idempotent_and_metadata_only(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert not (tmp_path / "http-mock-fixtures.json").exists()
    key = (tmp_path / "private-key.pem").read_bytes()
    assert local.main(["enable-mock-fixtures", "--state-dir", str(tmp_path)]) == 0
    first = json.loads(capsys.readouterr().out)
    marker = (tmp_path / "http-mock-fixtures.json").read_bytes()
    assert first == {
        "profile": "local-demo",
        "mock_fixtures": "enabled",
        "administrator_role": "organization_administrator",
        "organization_id": "local-demo",
        "environment_id": "dev",
    }
    assert local.main(["enable-mock-fixtures", "--state-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert (tmp_path / "http-mock-fixtures.json").read_bytes() == marker
    assert (tmp_path / "private-key.pem").read_bytes() == key


def test_cli_enable_respects_installation_lock(tmp_path, capsys):
    initialize(tmp_path)
    with local.installation_lock(tmp_path):
        assert local.main(["enable-mock-fixtures", "--state-dir", str(tmp_path)]) == 2
    assert not (tmp_path / "http-mock-fixtures.json").exists()
    assert not (tmp_path / "http-mock-fixtures.db").exists()
    assert "already in use" in capsys.readouterr().err


def test_cli_enable_failure_is_private(tmp_path, monkeypatch, capsys):
    initialize(tmp_path)
    monkeypatch.setattr(
        local, "enable_mock_fixtures", Mock(side_effect=RuntimeError("private=fixture-value"))
    )
    assert local.main(["enable-mock-fixtures", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "fixture-value" not in output.out + output.err


@pytest.mark.parametrize("damage", ["missing", "invalid-marker", "orphan"])
def test_profile_error_fails_before_runtime_or_api_listen(tmp_path, monkeypatch, damage):
    initialize(tmp_path)
    enable_mock_fixtures(tmp_path)
    database = tmp_path / "http-mock-fixtures.db"
    marker = tmp_path / "http-mock-fixtures.json"
    if damage == "missing":
        database.unlink()
    elif damage == "invalid-marker":
        marker.write_text('{"fixture":"private=fixture-value"}', encoding="utf-8")
    else:
        marker.unlink()
    runtime = Mock(side_effect=AssertionError("Must not start Temporal"))
    listener = Mock(side_effect=AssertionError("Must not bind API"))
    with asyncio.Runner() as runner:
        runner.get_loop()
        monkeypatch.setattr(local, "local_runtime", runtime)
        monkeypatch.setattr(local.socket, "socket", listener)
        with pytest.raises(ValueError):
            runner.run(local.serve_local(tmp_path))
    runtime.assert_not_called()
    listener.assert_not_called()
    assert database.exists() is (damage != "missing")


def test_enable_requires_initialized_installation(tmp_path, capsys):
    assert local.main(["enable-mock-fixtures", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "preserved" in output.err
    assert not (tmp_path / "http-mock-fixtures.json").exists()
