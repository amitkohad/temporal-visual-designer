"""Signed frozen-input inspection with actual session/project stores."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.contracts import Scope
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime
from nexusflow.tests.package_promotion.test_activation import content

OTHER = "55555555-5555-4555-8555-555555555555"
INPUT = {"public": "visible", "private": "hidden-input-sentinel", "password": "hidden-password"}


def project():
    value = content()
    value["workflows"][0]["input_schema"] = {
        "type": "object",
        "properties": {
            "public": {"type": "string", "x-nexusflow-inspect": True},
            "private": {"type": "string"},
            "password": {"type": "string", "x-nexusflow-inspect": True, "writeOnly": True},
        },
    }
    return value


@pytest.fixture
def inspected(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "inspection.db")
    runtime = DebugRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        created = client.post(
            BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project()}
        )
        assert created.status_code == 201, created.text
        accepted = client.post(PATH, json={**PAYLOAD, "input": INPUT})
        assert accepted.status_code == 202, accepted.text
        session = accepted.json()
        path = PATH + "/" + session["session_id"] + "/inputs"
        yield client, configured, data, runtime, session, path
    sessions.close()


def test_inspection_exposes_only_explicit_public_fields_and_never_calls_runtime(
    inspected, monkeypatch
):
    client, backend, _, runtime, session, path = inspected
    monkeypatch.setattr(runtime, "snapshot", Mock(side_effect=OSError("worker-is-down")))
    monkeypatch.setattr(runtime, "control", Mock(side_effect=OSError("worker-is-down")))
    backend.configurations.available = False
    before = backend.debug_sessions.connection.total_changes
    response = client.get(path)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["values"] == {"public": "visible"}
    assert value["source_revision"] == 1
    assert value["definition_digest"] == session["definition_digest"]
    assert "hidden-input-sentinel" not in response.text and "hidden-password" not in response.text
    assert value["truncated"] is False
    assert backend.debug_sessions.connection.total_changes == before
    runtime.snapshot.assert_not_called()
    runtime.control.assert_not_called()


def test_inspection_uses_original_schema_after_draft_widens_disclosure(inspected):
    client, _, _, _, _, path = inspected
    changed = project()
    changed["workflows"][0]["input_schema"]["properties"]["private"]["x-nexusflow-inspect"] = True
    saved = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "New schema", "content": changed},
    )
    assert saved.status_code == 200, saved.text
    result = client.get(path)
    assert result.status_code == 200, result.text
    assert result.json()["source_revision"] == 1
    assert result.json()["values"] == {"public": "visible"}
    assert "hidden-input-sentinel" not in result.text


def test_inspection_checks_owner_and_revocation_before_source_access(inspected, monkeypatch):
    client, backend, data, _, _, path = inspected
    repository, scope, grant, _, token = data
    repository.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    repository.save_grant(replace(grant, grant_id="other", principal_id=OTHER), expected_revision=0)
    source = Mock(side_effect=AssertionError("Unauthorized source read"))
    monkeypatch.setattr(backend.projects, "revision", source)
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-subject")
    assert client.get(path).status_code == 403
    client.headers["authorization"] = "Bearer " + token()
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(path).status_code == 403
    source.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "changed", "scope", "oversize", "provider"])
def test_inspection_fails_closed_for_original_source_binding(inspected, monkeypatch, case):
    from nexusflow.modules.project_versioning.types import VersioningError

    client, backend, data, _, _, path = inspected
    original = backend.projects.revision(data[1], "hr", 1)
    if case == "missing":
        lookup = Mock(side_effect=VersioningError("not_found", "hidden-input-sentinel"))
    elif case == "provider":
        lookup = Mock(side_effect=OSError("hidden-input-sentinel"))
    elif case == "scope":
        lookup = Mock(
            return_value=replace(
                original, scope=Scope(organization_id="foreign", environment_id="dev")
            )
        )
    elif case == "oversize":
        lookup = Mock(return_value=replace(original, content_json="x" * 4_000_001))
    else:
        import json

        changed = project()
        changed["workflows"][0]["input_schema"]["properties"]["private"]["x-nexusflow-inspect"] = (
            True
        )
        lookup = Mock(return_value=replace(original, content_json=json.dumps(changed)))
    monkeypatch.setattr(backend.projects, "revision", lookup)
    response = client.get(path)
    assert response.status_code == 503, response.text
    assert "hidden-input-sentinel" not in response.text


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_inspection_nondevelopment_scope_denied_before_provider_check(inspected, environment):
    client, backend, data, _, _, path = inspected
    repository, _, grant, _, _ = data
    repository.save_grant(
        replace(grant, scope=Scope(organization_id="acme", environment_id=environment)),
        expected_revision=0,
    )
    with TestClient(
        create_app(replace(backend, debug_sessions=None, debug_runtime=None)),
        raise_server_exceptions=False,
    ) as other:
        other.headers.update(client.headers)
        response = other.get(path.replace("/dev/", "/" + environment + "/"))
    assert response.status_code == 403, response.text


def test_unconfigured_provider_authorizes_before_availability(api):  # noqa: F811
    client, _, data, _ = api
    path = PATH + "/missing/inputs"
    assert client.get(path).status_code == 503
    repository, _, grant, _, _ = data
    repository.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(path).status_code == 403


def test_inspection_has_typed_openapi_and_no_store_response(inspected):
    client, _, _, _, _, path = inspected
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    schema = client.get(BASE + "/schema").json()
    route = next(
        value
        for key, value in schema["paths"].items()
        if key.endswith("/debug-sessions/{session_id}/inputs")
    )
    assert route["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DebugInputInspection")
    assert {"values", "redacted_paths", "truncated"} <= schema["components"]["schemas"][
        "DebugInputInspection"
    ]["properties"].keys()
