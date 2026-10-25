"""Actual CLI/socket human-task acceptance, without internal delivery shortcuts."""

import json
import time

import pytest

from nexusflow.apps.local_bootstrap import USER_ID
from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.runtime.test_local_launcher_integration import (
    ROOT,
    _cli,
    _ports,
    _run,
    _serve,
)

pytestmark = pytest.mark.integration


def _initialized(state):
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    enabled = _run(state, "enable-tasks")
    assert enabled.returncode == 0, enabled.stderr
    issued = _run(state, "token")
    assert issued.returncode == 0, issued.stderr
    return organization, {"Authorization": "Bearer " + issued.stdout.strip()}


def _publish(client, base, headers):
    payload = json.loads((ROOT / "examples/local-approval-project.json").read_text("utf-8"))
    response = client.post(base + "/projects", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    project = base + "/projects/" + payload["project_id"]
    response = client.post(
        project + "/releases",
        headers=headers,
        json={"source_revision": 1, "idempotency_key": "approval-release"},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        project + "/activation",
        headers=headers,
        json={
            "project_id": payload["project_id"],
            "release_id": response.json()["release_id"],
            "operation_id": "approval-activate",
            "configuration_revision": "local-v1",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    return project


def _wait_task(client, base, headers, execution):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(base + "/tasks", headers=headers, params={"run_id": execution})
        assert response.status_code == 200, response.text
        if response.json()["items"]:
            (task,) = response.json()["items"]
            assert task["eligible_approver_ids"] == [USER_ID]
            return task
        time.sleep(0.1)
    pytest.fail("Automatic task creation did not reach the HTTP inbox")


def test_enabled_cli_approval_survives_restart_and_automatically_completes(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    base = f"/api/v1/organizations/{organization}/environments/dev"
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    start = {"workflow_id": "approval-demo", "idempotency_key": "approval-start", "input": {}}
    with _serve(state, executable, api_port, temporal_port, log) as client:
        # Enabling providers never silently publishes a second sample project.
        assert client.get(base + "/projects/welcome-approval", headers=headers).status_code == 404
        project = _publish(client, base, headers)
        response = client.post(project + "/executions", headers=headers, json=start)
        assert response.status_code == 202, response.text
        accepted = response.json()
        task = _wait_task(client, base, headers, accepted["execution_id"])
        assert task["status"] == "pending"
        assert client.get(base + "/tasks").status_code == 401
        assert (
            client.get(base.replace("/dev", "/uat") + "/tasks", headers=headers).status_code == 403
        )
    with _serve(state, executable, api_port, temporal_port, log) as client:
        restored = _wait_task(client, base, headers, accepted["execution_id"])
        assert restored == task
        assert client.post(project + "/executions", headers=headers, json=start).json() == accepted
        url = base + "/tasks/" + task["task_id"]
        decision = {
            "command_id": "approve-local",
            "expected_revision": task["revision"],
            "decision": "approve",
        }
        response = client.post(url + "/decisions", headers=headers, json=decision)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "approved"
        assert (
            client.post(url + "/decisions", headers=headers, json=decision).json()
            == response.json()
        )
        execution_url = project + "/executions/" + accepted["execution_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get(execution_url, headers=headers)
            assert result.status_code == 200, result.text
            if result.json()["state"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Background task completion did not resume the actual Temporal execution")
        assert result.json()["completed_nodes"] == ["start", "approval", "end"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            audit = client.get(url + "/audit", headers=headers)
            assert audit.status_code == 200, audit.text
            kinds = [event["kind"] for event in audit.json()]
            if "completion_delivered" in kinds:
                break
            time.sleep(0.1)
        assert kinds == ["created", "decided", "completion_delivered"]
        assert len(client.get(base + "/tasks", headers=headers).json()["items"]) == 1
    identity = SQLiteIdentityRepository(state / "identity.db")
    try:
        identity.revoke_grant(
            Scope(organization_id=organization, environment_id="dev"),
            "local-task-participant",
            expected_revision=1,
        )
    finally:
        identity.close()
    assert _run(state, "enable-tasks").returncode == 0
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(url, headers=headers).status_code == 403
        assert client.post(url + "/decisions", headers=headers, json=decision).status_code == 403
        assert client.get(execution_url, headers=headers).json()["state"] == "completed"
    assert headers["Authorization"].split(" ")[1] not in log.read_text("utf-8", errors="replace")


@pytest.mark.parametrize("missing", ["tasks.db", "task-completions.db", "human-tasks.json"])
def test_enabled_cli_missing_task_state_fails_closed_without_recreation(tmp_path, missing):
    executable = _cli()
    state = tmp_path / "state"
    _initialized(state)
    removed = state / missing
    removed.unlink()
    assert _run(state, "enable-tasks").returncode != 0
    api_port, temporal_port = _ports()
    served = _run(
        state,
        "serve",
        "--api-port",
        str(api_port),
        "--temporal-port",
        str(temporal_port),
        "--temporal-cli",
        executable,
    )
    assert served.returncode != 0
    assert not removed.exists()
