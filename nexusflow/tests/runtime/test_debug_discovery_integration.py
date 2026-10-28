"""Actual CLI/socket discovery of paused sessions after complete launcher restart."""

import asyncio
import json

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.runtime.test_local_debugger_integration import _debug_state, _replay
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _run, _serve

pytestmark = pytest.mark.integration


def test_discover_saved_paused_sessions_after_cli_restart(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    initialized = _run(state, "init")
    assert initialized.returncode == 0, initialized.stderr
    organization = json.loads(initialized.stdout)["organization_id"]
    enabled = _run(state, "enable-debugger")
    assert enabled.returncode == 0, enabled.stderr
    token = _run(state, "token")
    assert token.returncode == 0, token.stderr
    headers = {"authorization": "Bearer " + token.stdout.strip()}
    base = f"/api/v1/organizations/{organization}/environments/dev"
    path = base + "/projects/welcome/debug-sessions"
    scope = Scope(organization_id=organization, environment_id="dev")
    identities = SQLiteIdentityRepository(state / "identity.db")
    try:
        placement = next(
            item for item in identities.environments(organization) if item.scope == scope
        )
    finally:
        identities.close()
    api_port, temporal_port = _ports()
    log = tmp_path / "discovery-launcher.log"
    request = {
        "source_revision": 1,
        "workflow_id": "welcome",
        "configuration_revision": "local-v1",
        "mode": "real",
        "input": {"private": "discovery-input-not-for-response"},
        "breakpoints": ["wait"],
    }
    sessions = []
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        activation = client.get(base + "/projects/welcome/activation")
        assert activation.status_code == 200, activation.text
        for operation in ("discover-a", "discover-b"):
            accepted = client.post(path, json={**request, "operation_id": operation})
            assert accepted.status_code == 202, accepted.text
            session = accepted.json()
            sessions.append(session)
            _debug_state(
                client,
                path + "/" + session["session_id"],
                "paused",
                temporal_port,
                placement.temporal_namespace,
                session,
            )
        original_page = client.get(path, params={"limit": 1})
        assert original_page.status_code == 200, original_page.text
        first_page = original_page.json()
        assert first_page["next_cursor"]
        assert "discovery-input-not-for-response" not in original_page.text
    # Rediscover by collection URI, without retaining a session-specific URL.
    with _serve(state, executable, api_port, temporal_port, log) as client:
        client.headers.update(headers)
        restored = client.get(path, params={"limit": 1})
        assert restored.status_code == 200, restored.text
        assert restored.json() == first_page
        second = client.get(path, params={"limit": 1, "cursor": first_page["next_cursor"]})
        assert second.status_code == 200, second.text
        assert second.json()["next_cursor"] is None
        discovered = first_page["items"] + second.json()["items"]
        assert {item["session_id"] for item in discovered} == {
            item["session_id"] for item in sessions
        }
        selected = second.json()["items"][0]
        selected_path = path + "/" + selected["session_id"]
        paused = _debug_state(
            client, selected_path, "paused", temporal_port, placement.temporal_namespace, selected
        )
        step = client.post(
            selected_path + "/commands",
            json={
                "command_id": "discovered-step",
                "node_id": paused["node_id"],
                "action": "step_over",
                "expected_revision": paused["revision"],
            },
        )
        assert step.status_code == 200, step.text
        _debug_state(
            client,
            selected_path,
            "completed",
            temporal_port,
            placement.temporal_namespace,
            selected,
        )
        asyncio.run(_replay(temporal_port, placement.temporal_namespace, selected))
        # Discovery lifecycle metadata deliberately remains started, not live status.
        final = client.get(path)
        assert final.status_code == 200, final.text
        assert all(item["state"] == "started" for item in final.json()["items"])
        assert client.get(base + "/projects/welcome/activation").json() == activation.json()
        assert "discovery-input-not-for-response" not in final.text
    logs = log.read_text(encoding="utf-8", errors="replace")
    assert token.stdout.strip() not in logs
    assert "discovery-input-not-for-response" not in logs
