"""Independent signed HTTP acceptance of Dev-only immutable debug sessions."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.durable_runtime.types import CommandReceipt, Snapshot
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content

OTHER = "55555555-5555-4555-8555-555555555555"


def request(**changes):
    return {
        "operation_id": "debug-1",
        "source_revision": 1,
        "workflow_id": "onboarding",
        "configuration_revision": "cfg-1",
        "input": {"private": "never-in-metadata"},
        "breakpoints": [],
        "mode": "real",
        **changes,
    }


class MetadataRuntime:
    def __init__(self):
        self.plans = {}
        self.calls = []
        self.fail = False

    async def start(self, placement, execution_id, plan):
        self.calls.append("start")
        if self.fail:
            raise OSError("sensitive-runtime-provider-detail")
        self.plans[execution_id] = plan
        return "exact-temporal-run"

    async def snapshot(self, placement, execution_id, run_id):
        self.calls.append("snapshot")
        assert run_id == "exact-temporal-run"
        plan = self.plans[execution_id]
        return Snapshot(
            plan.binding.release_id,
            plan.definition_digest,
            plan.binding.runtime_version,
            "end",
            "paused",
            0,
            ("start",),
        )

    async def control(self, placement, execution_id, run_id, command):
        self.calls.append("control")
        return CommandReceipt(command.command_id, 1)


@pytest.fixture
def debug(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    assert (
        original.post(
            BASE + "/projects",
            json={
                "project_id": "hr",
                "name": "HR",
                "content": content(),
            },
        ).status_code
        == 201
    )
    repository = SQLiteDebugSessionRepository(tmp_path / "debug-acceptance.db")
    runtime = MetadataRuntime()
    configured = replace(backend, debug_sessions=repository, debug_runtime=runtime)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        yield client, configured, data, runtime
    repository.close()


def start(client, **changes):
    return client.post(BASE + "/projects/hr/debug-sessions", json=request(**changes))


def test_revision_debug_requires_no_release_and_retry_keeps_original_snapshot(debug):
    client, backend, data, runtime = debug
    result = start(client)
    assert result.status_code == 202, result.text
    session = result.json()
    assert session["source_revision"] == 1
    assert session["temporal_run_id"] == "exact-temporal-run"
    assert backend.activations.current(data[1], "hr") is None
    assert "never-in-metadata" not in result.text
    saved = client.put(
        BASE + "/projects/hr",
        json={
            "expected_revision": 1,
            "summary": "New draft",
            "content": content("B"),
        },
    )
    assert saved.status_code == 200, saved.text
    backend.configurations.available = False
    assert start(client).json() == session
    assert runtime.calls == ["start"]
    assert start(client, input={"changed": True}).status_code == 409
    assert start(client, operation_id="debug-2", source_revision=2).status_code == 422
    status = client.get(BASE + "/projects/hr/debug-sessions/" + session["session_id"])
    assert status.status_code == 200, status.text
    assert status.json()["snapshot"]["definition_digest"] == session["definition_digest"]
    assert "never-in-metadata" not in status.text


def test_status_control_and_retry_reauthorize_and_enforce_owner(debug):
    client, _, data, runtime = debug
    session = start(client).json()
    path = BASE + "/projects/hr/debug-sessions/" + session["session_id"]
    repository, scope, grant, _, token = data
    repository.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    repository.save_grant(replace(grant, grant_id="other", principal_id=OTHER), expected_revision=0)
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-pairwise-subject")
    calls = list(runtime.calls)
    assert client.get(path).status_code == 403
    assert (
        client.post(
            path + "/commands",
            json={
                "command_id": "step",
                "node_id": "end",
                "action": "step_over",
                "expected_revision": 0,
            },
        ).status_code
        == 403
    )
    assert start(client).status_code == 403
    assert runtime.calls == calls
    client.headers["authorization"] = "Bearer " + token()
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(path).status_code == 403
    assert start(client).status_code == 403
    assert runtime.calls == calls


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_nondevelopment_scope_is_denied_before_provider_access(debug, environment):
    client, backend, data, runtime = debug
    repository, _, grant, _, _ = data
    scope = Scope(organization_id="acme", environment_id=environment)
    repository.save_grant(replace(grant, scope=scope), expected_revision=0)
    configured = replace(backend, debug_sessions=None, debug_runtime=None)
    with TestClient(create_app(configured), raise_server_exceptions=False) as other:
        other.headers.update(client.headers)
        response = other.post(
            BASE.replace("/dev", "/" + environment) + "/projects/hr/debug-sessions", json=request()
        )
    assert response.status_code == 403, response.text
    assert runtime.calls == []


def test_explicit_mode_invalid_input_and_configuration_fail_before_dispatch(debug):
    client, backend, data, runtime = debug
    payload = request()
    del payload["mode"]
    assert client.post(BASE + "/projects/hr/debug-sessions", json=payload).status_code == 422
    assert start(client, mode="simulation").status_code == 422
    assert start(client, workflow_id="missing").status_code == 404
    assert start(client, breakpoints=["missing"]).status_code == 422
    backend.configurations.available = False
    assert start(client).status_code == 422
    assert backend.debug_sessions.operation(data[1], "hr", "debug-1") is None
    assert runtime.calls == []


def test_runtime_failure_is_sanitized_and_preserves_retryable_intent(debug):
    client, backend, data, runtime = debug
    runtime.fail = True
    response = start(client)
    assert response.status_code == 503, response.text
    assert "sensitive-runtime" not in response.text
    record = backend.debug_sessions.operation(data[1], "hr", "debug-1")
    assert record.session.state == "dispatch_pending"
    runtime.fail = False
    recovered = start(client)
    assert recovered.status_code == 202, recovered.text
    assert recovered.json()["execution_id"] == record.session.execution_id
