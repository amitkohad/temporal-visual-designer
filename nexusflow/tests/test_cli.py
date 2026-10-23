"""Exercise process boundaries rather than calling the CLI implementation."""

import json
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_validation_cli_accepts_valid_fixture():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nexusflow.apps.cli",
            "workflow",
            str(FIXTURES / "purchasing.workflow.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_validation_cli_rejects_secret_without_disclosing_it(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text('{"secret":"private-token"}')
    result = subprocess.run(
        [sys.executable, "-m", "nexusflow.apps.cli", "workflow", str(source)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["valid"] is False
    assert "private-token" not in result.stdout + result.stderr


def test_validation_cli_missing_file_is_safe(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "nexusflow.apps.cli", "workflow", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stdout + result.stderr
