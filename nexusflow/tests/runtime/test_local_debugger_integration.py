"""Actual local launcher debugging persists through a full process/server restart."""

import asyncio
import json
import time

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _run, _serve

pytestmark = pytest.mark.integration


def _debug_state(client, path, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        if response.json()["snapshot"]["state"] == expected:
            return response.json()["snapshot"]
        time.sleep(0.1)
    pytest.fail("Local debugger did not reach expected state")


async def _replay(port, namespace, session):
    client = await Client.connect(f"127.0.0.1:{port}", namespace=namespace)
    handle = client.get_workflow_handle(session["execution_id"], run_id=session["temporal_run_id"])
    result = await handle.result()
    assert result["binding"]["release_id"] == session["session_id"]
    await Replayer(workflows=[ProofWorkflow]).replay_workflow(await handle.fetch_history())


def test_local_cli_debugger_preserves_paused_session_and_receipts_on_restart(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    enabled = _run(state, "enable-debugger")
    assert enabled.returncode == 0, enabled.stderr
    assert (state / "debugger.json").is_file()
    assert (state / "debug-sessions.db").is_file()
    token = _run(state, "token")
    assert token.returncode == 0, token.stderr
    headers = {"authorization": "Bearer " + token.stdout.strip()}
    base = f"/api/v1/organizations/{organization}/environments/dev"
    scope = Scope(organization_id=organization, environment_id="dev")
    identities = SQLiteIdentityRepository(state / "identity.db")
    try:
        placement = next(
            item for item in identities.environments(organization) if item.scope == scope
        )
    finally:
        identities.close()
    api_port, temporal_port = _ports()
    log = tmp_path / "debug-launcher.log"
    path = base + "/projects/welcome/debug-sessions"
    request = {
        "operation_id": "restart-debug",
        "source_revision": 1,
        "workflow_id": "welcome",
        "configuration_revision": "local-v1",
        "input": {"private": "debug-request-not-for-logs"},
        "breakpoints": ["wait"],
        "mode": "real",
    }
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        activation = client.get(base + "/projects/welcome/activation")
        assert activation.status_code == 200, activation.text
        accepted = client.post(path, json=request)
        assert accepted.status_code == 202, accepted.text
        session = accepted.json()
        session_path = path + "/" + session["session_id"]
        paused = _debug_state(client, session_path, "paused")
        assert paused["node_id"] == "wait" and paused["revision"] == 0
        assert "debug-request-not-for-logs" not in accepted.text + json.dumps(paused)
        assert (
            client.get(
                base.replace("/dev", "/uat") + "/projects/welcome/debug-sessions/x"
            ).status_code
            == 403
        )
    # Restart the actual launcher, Temporal server and worker with original state/ports/token.
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        assert _debug_state(client, session_path, "paused") == paused
        assert client.post(path, json=request).json() == session
        assert client.get(base + "/projects/welcome/activation").json() == activation.json()
        command = {
            "command_id": "step-wait",
            "node_id": "wait",
            "action": "step_over",
            "expected_revision": 0,
        }
        receipt = client.post(session_path + "/commands", json=command)
        assert receipt.status_code == 200, receipt.text
        finished = _debug_state(client, session_path, "completed")
        assert finished["release_id"] == session["session_id"]
        assert client.post(session_path + "/commands", json=command).json() == receipt.json()
        changed = client.post(session_path + "/commands", json={**command, "action": "continue"})
        assert changed.status_code == 409, changed.text
        asyncio.run(_replay(temporal_port, placement.temporal_namespace, session))

        next_session = client.post(path, json={**request, "operation_id": "continue-debug"})
        assert next_session.status_code == 202, next_session.text
        next_path = path + "/" + next_session.json()["session_id"]
        _debug_state(client, next_path, "paused")
        continuation = client.post(
            next_path + "/commands",
            json={**command, "command_id": "continue-wait", "action": "continue"},
        )
        assert continuation.status_code == 200, continuation.text
        _debug_state(client, next_path, "completed")
        normal = client.post(
            base + "/projects/welcome/executions",
            json={"workflow_id": "welcome", "idempotency_key": "normal-after-debug"},
        )
        assert normal.status_code == 202, normal.text
        assert normal.json()["release_id"] == activation.json()["release_id"]
        assert client.get(base + "/projects/welcome/activation").json() == activation.json()
        identities = SQLiteIdentityRepository(state / "identity.db")
        try:
            identities.revoke_grant(scope, "local-author", expected_revision=1)
        finally:
            identities.close()
        assert client.get(session_path).status_code == 403
        assert client.post(session_path + "/commands", json=command).status_code == 403
    repeated = _run(state, "enable-debugger")
    assert repeated.returncode == 0, repeated.stderr
    identities = SQLiteIdentityRepository(state / "identity.db")
    try:
        assert not any(item.grant_id == "local-author" for item in identities.grants(scope))
    finally:
        identities.close()
    logs = log.read_text(encoding="utf-8", errors="replace")
    assert token.stdout.strip() not in logs
    assert "debug-request-not-for-logs" not in logs
    assert "PRIVATE KEY" not in logs
