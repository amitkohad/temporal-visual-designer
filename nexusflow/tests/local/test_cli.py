"""Independent CLI boundary and installation locking checks without a server."""

import asyncio
import json
from unittest.mock import Mock

import pytest

from nexusflow.apps import local


def test_installation_lock_rejects_another_owner_and_releases(tmp_path):
    with local.installation_lock(tmp_path):
        with pytest.raises(local.LocalCommandError):
            with local.installation_lock(tmp_path):
                raise AssertionError("Second owner must never enter")
    with local.installation_lock(tmp_path):
        assert (tmp_path / ".launcher.lock").exists()


def test_invalid_ports_rejected_before_state_creation(tmp_path):
    directory = tmp_path / "not-created"
    with pytest.raises(local.LocalCommandError):
        asyncio.run(local.serve_local(directory, api_port=8000, temporal_port=8000))
    assert not directory.exists()


@pytest.mark.parametrize("port", ["0", "1023", "65536", "bad", "-1"])
def test_cli_rejects_invalid_port_arguments(port):
    with pytest.raises(SystemExit) as error:
        local.main(["serve", "--api-port", port])
    assert error.value.code == 2


def test_nonloopback_host_option_is_not_available():
    with pytest.raises(SystemExit) as error:
        local.main(["serve", "--host", "0.0.0.0"])
    assert error.value.code == 2


def test_provider_exceptions_do_not_leak_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        local, "issue_token", Mock(side_effect=RuntimeError("password=private-value"))
    )
    assert local.main(["token", "--state-dir", str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert "private-value" not in output.err + output.out


def test_real_initialization_and_token_commands(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["profile"] == "local-demo"
    assert initialized["environment_id"] == "dev"
    assert local.main(["token", "--state-dir", str(tmp_path)]) == 0
    token = capsys.readouterr().out.strip()
    assert token.count(".") == 2
    assert token not in initialized.values()


def test_stop_request_is_installation_bound(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert local.main(["stop", "--state-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "stop_requested"
    assert (tmp_path / ".stop-request").read_text() == local.load_settings(tmp_path).installation_id


def test_read_only_token_does_not_require_serve_lock(tmp_path, capsys):
    assert local.main(["init", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    with local.installation_lock(tmp_path):
        assert local.main(["token", "--state-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().count(".") == 2
