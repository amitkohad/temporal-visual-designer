"""Real CLI/socket acceptance, including persistence across launcher restarts."""

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]


def _cli():
    executable = os.environ.get("NEXUSFLOW_TEMPORAL_CLI") or shutil.which("temporal")
    if not executable:
        pytest.skip("Install Temporal CLI or set NEXUSFLOW_TEMPORAL_CLI")
    return executable


def _command(state, *arguments):
    return [sys.executable, "-m", "nexusflow.apps.local", *arguments, "--state-dir", str(state)]


def _options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _run(state, *arguments):
    return subprocess.run(
        _command(state, *arguments),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        **_options(),
    )


def _ports():
    with contextlib.ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(2)]
        for bound in sockets:
            bound.bind(("127.0.0.1", 0))
        return [bound.getsockname()[1] for bound in sockets]


def _wait_health(client, process):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert process.poll() is None, "Launcher exited before HTTP readiness"
        try:
            if client.get("/health").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    pytest.fail("Launcher HTTP readiness deadline exceeded")


@contextlib.contextmanager
def _serve(state, executable, api_port, temporal_port, log):
    with log.open("ab") as output:
        process = subprocess.Popen(
            _command(
                state,
                "serve",
                "--api-port",
                str(api_port),
                "--temporal-port",
                str(temporal_port),
                "--temporal-cli",
                executable,
            ),
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            **_options(),
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=2) as client:
                _wait_health(client, process)
                yield client
        finally:
            if process.poll() is None:
                try:
                    stopped = _run(state, "stop")
                    assert stopped.returncode == 0, stopped.stderr
                    process.wait(timeout=20)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
                        pytest.fail("Launcher did not shut down gracefully")


def test_cli_real_socket_execution_survives_restart_and_rejects_second_owner(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    issued = _run(state, "token")
    assert issued.returncode == 0, issued.stderr
    token = issued.stdout.strip()
    assert len(token.split(".")) == 3
    headers = {"Authorization": "Bearer " + token}
    base = f"/api/v1/organizations/{organization}/environments/dev"
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    payload = {
        "workflow_id": "welcome",
        "idempotency_key": "restart-smoke",
        "input": {"private": "launcher-secret-do-not-log"},
    }
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(base + "/projects").status_code == 401
        project = client.get(base + "/projects/welcome", headers=headers)
        assert project.status_code == 200
        activation = client.get(base + "/projects/welcome/activation", headers=headers)
        assert activation.status_code == 200
        contender = _run(
            state,
            "serve",
            "--api-port",
            str(api_port),
            "--temporal-port",
            str(temporal_port),
            "--temporal-cli",
            executable,
        )
        assert contender.returncode != 0
        assert _run(state, "token").returncode == 0
        response = client.post(base + "/projects/welcome/executions", headers=headers, json=payload)
        assert response.status_code == 202, response.text
        accepted = response.json()
        execution_path = base + "/projects/welcome/executions/" + accepted["execution_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(execution_path, headers=headers)
            assert status.status_code == 200, status.text
            if status.json()["state"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Real Temporal workflow did not complete")
        assert "launcher-secret-do-not-log" not in response.text + status.text
        assert (
            client.get(base.replace("/dev", "/uat") + "/projects", headers=headers).status_code
            == 403
        )
    # Restart opens the same actual databases and uses the original token and receipts.
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(base + "/projects/welcome", headers=headers).json() == project.json()
        assert (
            client.get(base + "/projects/welcome/activation", headers=headers).json()
            == activation.json()
        )
        assert client.get(execution_path, headers=headers).json()["state"] == "completed"
        retry = client.post(base + "/projects/welcome/executions", headers=headers, json=payload)
        assert retry.status_code == 202, retry.text
        assert retry.json() == accepted
    logs = log.read_text(encoding="utf-8", errors="replace")
    assert token not in logs
    assert "launcher-secret-do-not-log" not in logs
    assert "PRIVATE KEY" not in logs
