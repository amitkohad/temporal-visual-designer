"""Independent signed HTTP tests for frozen, fail-closed debug input inspection."""

# ruff: noqa: F811
import copy
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_session_acceptance import OTHER, MetadataRuntime, request
from nexusflow.tests.runtime.test_debug_session_integration import timer_project

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "display": {"type": "string", "x-nexusflow-inspect": True},
        "protected": {"type": "string", "writeOnly": True, "x-nexusflow-inspect": True},
        "unclassified": {"type": "string"},
        "nested": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "x-nexusflow-inspect": True},
                "credential": {"type": "string", "format": "password", "x-nexusflow-inspect": True},
            },
        },
        "collection": {
            "type": "array",
            "x-nexusflow-inspect": True,
            "items": {"type": "string", "x-nexusflow-inspect": True},
        },
        "conditional": {"type": "string", "x-nexusflow-inspect": True, "allOf": [{"minLength": 1}]},
    },
}
INPUTS = {
    "display": "public-employee",
    "protected": "private-credential-sentinel",
    "unclassified": "unclassified-sentinel",
    "nested": {"count": 7, "credential": "nested-password-sentinel"},
    "collection": ["array-private-sentinel"],
    "conditional": "composed-private-sentinel",
    "secret-dynamic-key-sentinel": "dynamic-private-value-sentinel",
}


def project(schema=None):
    result = timer_project(0)
    for workflow in result["workflows"]:
        workflow["input_schema"] = copy.deepcopy(INPUT_SCHEMA if schema is None else schema)
    return result


@pytest.fixture
def inspection(api, tmp_path):
    original, backend, data, _ = api
    response = original.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project()}
    )
    assert response.status_code == 201, response.text
    sessions = SQLiteDebugSessionRepository(tmp_path / "inspection-acceptance.db")
    runtime = MetadataRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        accepted = client.post(BASE + "/projects/hr/debug-sessions", json=request(input=INPUTS))
        assert accepted.status_code == 202, accepted.text
        session = accepted.json()
        path = BASE + "/projects/hr/debug-sessions/" + session["session_id"] + "/inputs"
        yield client, configured, data, runtime, session, path
    sessions.close()


def assert_redacted(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"] == {"display": "public-employee", "nested": {"count": 7}}
    for value in (
        "private-credential-sentinel",
        "unclassified-sentinel",
        "nested-password-sentinel",
        "array-private-sentinel",
        "composed-private-sentinel",
        "secret-dynamic-key-sentinel",
        "dynamic-private-value-sentinel",
    ):
        assert value not in response.text
    assert body["redacted_paths"]
    assert len(response.content) <= 16384
    return body


def test_frozen_source_policy_and_inputs_do_not_follow_current_draft_or_runtime(inspection):
    client, backend, data, runtime, session, path = inspection
    before = list(runtime.calls)
    first = assert_redacted(client.get(path))
    assert first["session_id"] == session["session_id"]
    assert first["definition_digest"] == session["definition_digest"]
    changed_schema = copy.deepcopy(INPUT_SCHEMA)
    changed_schema["properties"]["unclassified"]["x-nexusflow-inspect"] = True
    saved = client.put(
        BASE + "/projects/hr",
        json={
            "expected_revision": 1,
            "summary": "Different inspection policy",
            "content": project(changed_schema),
        },
    )
    assert saved.status_code == 200, saved.text
    backend.configurations.available = False
    runtime.fail = True
    assert assert_redacted(client.get(path)) == first
    assert runtime.calls == before
    assert backend.activations.current(data[1], "hr") is None


def test_owner_and_fresh_permission_checks_precede_source_or_runtime_reads(inspection, monkeypatch):
    client, backend, data, runtime, _, path = inspection
    repository, scope, grant, _, token = data
    repository.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    repository.save_grant(
        replace(grant, grant_id="other-inspector", principal_id=OTHER), expected_revision=0
    )
    monkeypatch.setattr(
        backend.projects, "revision", lambda *args: pytest.fail("Unauthorized source read")
    )
    calls = list(runtime.calls)
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-inspector")
    assert client.get(path).status_code == 403
    client.headers["authorization"] = "Bearer " + token()
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(path).status_code == 403
    for environment in ("uat", "prod"):
        assert client.get(path.replace("/dev/", "/" + environment + "/")).status_code == 403
    assert runtime.calls == calls


def test_mismatched_original_schema_or_source_failure_is_sanitized(inspection, monkeypatch):
    client, backend, data, runtime, _, path = inspection
    original = backend.projects.revision(data[1], "hr", 1)
    altered = json.loads(original.content_json)
    altered["workflows"][0]["input_schema"]["properties"]["protected"].pop("writeOnly")
    monkeypatch.setattr(
        backend.projects,
        "revision",
        lambda *args: replace(original, content_json=json.dumps(altered)),
    )
    failed = client.get(path)
    assert failed.status_code == 503, failed.text
    assert "private-credential-sentinel" not in failed.text

    def unavailable(*args):
        raise OSError("private-source-provider-sentinel")

    monkeypatch.setattr(backend.projects, "revision", unavailable)
    failed = client.get(path)
    assert failed.status_code == 503
    assert "private-source-provider-sentinel" not in failed.text
    assert runtime.calls == ["start"]


def test_large_public_data_is_bounded_without_leaking_hidden_names(api, tmp_path):
    original, backend, _, _ = api
    properties = {
        f"public_{index}": {"type": "string", "x-nexusflow-inspect": True} for index in range(30)
    }
    properties["protected"] = {"type": "string", "x-sensitive": True, "x-nexusflow-inspect": True}
    schema = {"type": "object", "properties": properties}
    created = original.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project(schema)}
    )
    assert created.status_code == 201, created.text
    sessions = SQLiteDebugSessionRepository(tmp_path / "large-inspection.db")
    runtime = MetadataRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    values = {f"public_{index}": "x" * 900 for index in range(30)}
    values.update(
        {"protected": "hidden-large-secret", "unknown-secret-key": "hidden-unknown-value"}
    )
    try:
        with TestClient(create_app(configured), raise_server_exceptions=False) as client:
            client.headers.update(original.headers)
            accepted = client.post(BASE + "/projects/hr/debug-sessions", json=request(input=values))
            assert accepted.status_code == 202, accepted.text
            response = client.get(
                BASE + "/projects/hr/debug-sessions/" + accepted.json()["session_id"] + "/inputs"
            )
            assert response.status_code == 200, response.text
            assert len(response.content) <= 16384
            assert response.json()["truncated"] is True
            assert len(response.json()["redacted_paths"]) <= 100
            for marker in ("hidden-large-secret", "unknown-secret-key", "hidden-unknown-value"):
                assert marker not in response.text
    finally:
        sessions.close()
