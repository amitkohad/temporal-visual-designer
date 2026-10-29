"""Signed frozen literal step inputs require no runtime access."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.activity_catalog.types import ActivityManifest
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.tests.activity_catalog.test_validation import archive
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_outputs import output_project, output_schema
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime


def step_input_schema():
    schema = output_schema()
    fields = schema["properties"]["rows"]["items"]["properties"]
    fields["credential"] = fields.pop("password")
    return schema


def step_input_values():
    return {
        "status": "ready",
        "secret": "step-input-secret",
        "unclassified": "unclassified-input-secret",
        "dynamic-private-key": "dynamic-input-secret",
        "rows": [{"name": "first", "credential": "credential-input-secret"}],
    }


def step_input_bundle(schema=None):
    descriptor = json.loads(
        (Path(__file__).parents[1] / "fixtures/activity-descriptor.json").read_text()
    )
    descriptor["connection_requirements"] = []
    descriptor["input_schema"] = step_input_schema() if schema is None else schema
    descriptor["output_schema"] = {"type": "object"}
    return validate_bundle(
        archive({"src/activity.py": b"def run(value):\n    return value\n"}, descriptor=descriptor)
    )


def step_input_project(reference, values=None):
    project = output_project(reference)
    project["workflows"][0]["nodes"][1]["input_mapping"] = (
        step_input_values() if values is None else values
    )
    return project


class NoReadRuntime(DebugRuntime):
    async def snapshot(self, *args):
        raise AssertionError("Frozen step inputs must not query runtime")

    async def control(self, *args):
        raise AssertionError("Frozen step inputs must not control runtime")

    async def step_output(self, *args):
        raise AssertionError("Frozen step inputs must not query output")


@pytest.fixture
def step_inputs(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    bundle = step_input_bundle()
    imported = backend.intake.put(data[1], bundle, "publisher")
    entry = backend.catalog.publish(imported, bundle.archive, "publisher")
    response = original.post(
        BASE + "/projects",
        json={"project_id": "hr", "name": "HR", "content": step_input_project(entry.reference)},
    )
    assert response.status_code == 201, response.text
    sessions = SQLiteDebugSessionRepository(tmp_path / "step-inputs.db")
    runtime = NoReadRuntime()
    backend = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        response = client.post(PATH, json=PAYLOAD)
        assert response.status_code == 202, response.text
        yield client, backend, data, runtime, response.json(), entry
    sessions.close()


def step_input_path(session, node="inspect"):
    return PATH + "/" + session["session_id"] + "/steps/" + node + "/input"


def test_pinned_literal_projection_without_runtime_access(step_inputs):
    client, backend, data, runtime, session, entry = step_inputs
    response = client.get(step_input_path(session))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["values"] == {"status": "ready", "rows": [{"name": "first"}]}
    assert result["activity_ref"] == entry.reference.model_dump(mode="json")
    assert result["source_revision"] == session["source_revision"]
    assert result["definition_digest"] == session["definition_digest"]
    assert result["node_id"] == "inspect"
    assert set(result["redacted_paths"]) == {"", "/secret", "/unclassified", "/rows/0/credential"}
    for private in (
        "step-input-secret",
        "unclassified-input-secret",
        "dynamic-private-key",
        "dynamic-input-secret",
        "credential-input-secret",
    ):
        assert private not in response.text
    runtime.start = None
    assert client.get(step_input_path(session)).json() == result
    assert backend.activations.current(data[1], "hr") is None
    assert runtime.calls == []


def test_later_draft_and_withdrawal_do_not_change_saved_inputs(step_inputs):
    client, backend, data, _, session, entry = step_inputs
    original = client.get(step_input_path(session))
    assert original.status_code == 200
    changed = step_input_project(entry.reference, {"status": "new literal", "secret": "changed"})
    assert (
        client.put(
            BASE + "/projects/hr",
            json={"expected_revision": 1, "summary": "Edit", "content": changed},
        ).status_code
        == 200
    )
    backend.catalog.transition(data[1], entry.reference, "deprecated", 1, "publisher")
    backend.catalog.transition(data[1], entry.reference, "withdrawn", 2, "publisher")
    assert client.get(step_input_path(session)).json() == original.json()


def test_owner_and_fresh_revocation_guards(step_inputs):
    client, _, data, _, session, _ = step_inputs
    client.headers["authorization"] = "Bearer " + data[4](sub="other-pairwise-subject")
    assert client.get(step_input_path(session)).status_code == 403
    client.headers["authorization"] = "Bearer " + data[4]()
    assert client.get(step_input_path(session)).status_code == 200
    store, scope, grant, _, _ = data
    store.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(step_input_path(session)).status_code == 403


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_denial(step_inputs, environment):
    client, _, data, _, session, _ = step_inputs
    store, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    store.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    assert (
        client.get(step_input_path(session).replace("/dev/", f"/{environment}/")).status_code == 403
    )


@pytest.mark.parametrize(
    "node, status", [("start", 409), ("later", 409), ("missing", 404), ("bad!node", 422)]
)
def test_non_activity_missing_and_invalid_nodes(step_inputs, node, status):
    client, _, _, _, session, _ = step_inputs
    response = client.get(step_input_path(session, node))
    assert response.status_code == status, response.text


@pytest.mark.parametrize("mutation", ["missing-schema", "changed-schema"])
def test_missing_or_changed_schema_fails_closed(step_inputs, mutation):
    client, backend, _, _, session, _ = step_inputs
    if mutation == "missing-schema":
        backend.catalog._db.execute("DELETE FROM activity_catalog")
    else:
        row = backend.catalog._db.execute("SELECT manifest FROM activity_catalog").fetchone()
        manifest = ActivityManifest.model_validate_json(row[0]).model_dump(mode="json")
        manifest["descriptor"]["input_schema"]["properties"]["secret"].pop("x-secret")
        backend.catalog._db.execute(
            "UPDATE activity_catalog SET manifest=?", (json.dumps(manifest),)
        )
    backend.catalog._db.commit()
    response = client.get(step_input_path(session))
    assert response.status_code == 503, response.text
    assert "step-input-secret" not in response.text


def test_pending_dispatch_rejected_without_runtime_read(step_inputs, monkeypatch):
    client, backend, data, runtime, _, _ = step_inputs

    async def unavailable(*args):
        raise OSError("private-start-detail")

    monkeypatch.setattr(runtime, "start", unavailable)
    assert (
        client.post(PATH, json={**PAYLOAD, "operation_id": "pending-step-input"}).status_code == 503
    )
    pending = backend.debug_sessions.operation(data[1], "hr", "pending-step-input")
    response = client.get(step_input_path(pending.session.model_dump(mode="json")))
    assert response.status_code == 409, response.text


def start_changed_mapping(client, session, entry, mapping, operation):
    saved = client.put(
        BASE + "/projects/hr",
        json={
            "expected_revision": session["source_revision"],
            "summary": "Change literal mapping",
            "content": step_input_project(entry.reference, mapping),
        },
    )
    assert saved.status_code == 200, saved.text
    started = client.post(PATH, json={**PAYLOAD, "operation_id": operation, "source_revision": 2})
    assert started.status_code == 202, started.text
    return started.json()


def test_scalar_and_array_limits_are_enforced_without_runtime_access(step_inputs):
    client, _, _, _, session, entry = step_inputs
    session = start_changed_mapping(
        client,
        session,
        entry,
        {"status": "a" * 1025, "rows": [{"name": str(index)} for index in range(101)]},
        "bounded-input",
    )
    response = client.get(step_input_path(session))
    assert response.status_code == 200, response.text
    result = response.json()
    assert "status" not in result["values"]
    assert len(result["values"]["rows"]) == 100
    assert result["truncated"] is True
    assert set(result["redacted_paths"]) == {"/status", "/rows"}
    assert len(response.content) <= 16384


def test_complete_response_budget_falls_back_to_empty_projection(step_inputs):
    client, _, _, _, session, entry = step_inputs
    session = start_changed_mapping(
        client,
        session,
        entry,
        {"rows": [{"name": str(index) + "x" * 200} for index in range(100)]},
        "aggregate-input",
    )
    response = client.get(step_input_path(session))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["values"] == {}
    assert result["redacted_paths"] == [""]
    assert result["truncated"] is True
    assert len(response.content) <= 16384
