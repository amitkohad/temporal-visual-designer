"""Signed output inspection with actual pinned catalog schemas and a trusted runtime fake."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.activity_catalog.types import ActivityManifest
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import DebugError
from nexusflow.modules.durable_runtime.types import DebugStepOutput, Snapshot
from nexusflow.tests.activity_catalog.test_validation import archive
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_sessions import PATH, PAYLOAD, DebugRuntime
from nexusflow.tests.package_promotion.test_activation import content


def output_schema():
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "x-nexusflow-inspect": True},
            "secret": {"type": "string", "x-nexusflow-inspect": True, "x-secret": True},
            "unclassified": {"type": "string"},
            "rows": {
                "type": "array",
                "x-nexusflow-inspect": True,
                "x-nexusflow-inspect-array": True,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "x-nexusflow-inspect": True},
                        "password": {"type": "string", "format": "password"},
                    },
                },
            },
        },
    }


def output_bundle(schema=None):
    descriptor = json.loads(
        (Path(__file__).parents[1] / "fixtures/activity-descriptor.json").read_text()
    )
    descriptor["connection_requirements"] = []
    descriptor["input_schema"] = {"type": "object"}
    descriptor["output_schema"] = output_schema() if schema is None else schema
    return validate_bundle(
        archive({"src/activity.py": b"def run(value):\n    return value\n"}, descriptor=descriptor)
    )


def output_project(reference):
    project = content()
    project["activity_refs"] = [reference.model_dump(mode="json")]
    workflow = project["workflows"][0]
    nodes = workflow["nodes"]
    workflow["nodes"] = [
        nodes[0],
        {
            "node_id": "inspect",
            "kind": "activity",
            "named_ports": {"inputs": ["in"], "outputs": ["out"]},
            "input_mapping": {},
            "activity_ref": reference.model_dump(mode="json"),
        },
        {
            "node_id": "later",
            "kind": "wait",
            "named_ports": {"inputs": ["in"], "outputs": ["out"]},
            "input_mapping": {"seconds": 0},
        },
        nodes[-1],
    ]
    names = [node["node_id"] for node in workflow["nodes"]]
    workflow["edges"] = [
        {
            "source_node_id": source,
            "source_port": "out",
            "target_node_id": target,
            "target_port": "in",
        }
        for source, target in zip(names, names[1:], strict=False)
    ]
    return project


class OutputRuntime(DebugRuntime):
    def __init__(self):
        super().__init__()
        self.outputs = []
        self.completed = True
        self.fail = False
        self.wrong_binding = False
        self.raw = json.dumps(
            {
                "status": "ready",
                "secret": "output-secret",
                "unclassified": "unclassified-secret",
                "dynamic-private-key": "dynamic-secret",
                "rows": [{"name": "first", "password": "password-secret"}],
            }
        )

    async def step_output(self, placement, execution_id, run_id, node_id):
        self.outputs.append((placement, execution_id, run_id, node_id))
        if self.fail:
            raise OSError("private-provider-diagnostics")
        if not self.completed:
            raise DebugError("conflict", "Output is not completed")
        plan = self.plans[execution_id]
        return DebugStepOutput(
            Snapshot(
                "wrong" if self.wrong_binding else plan.binding.release_id,
                plan.definition_digest,
                plan.binding.runtime_version,
                "later",
                "paused",
                1,
                ("start", "inspect"),
            ),
            node_id,
            self.raw,
        )


@pytest.fixture
def outputs(api, tmp_path):  # noqa: F811
    original, backend, data, _ = api
    bundle = output_bundle()
    imported = backend.intake.put(data[1], bundle, "publisher")
    entry = backend.catalog.publish(imported, bundle.archive, "publisher")
    project = output_project(entry.reference)
    response = original.post(
        BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": project}
    )
    assert response.status_code == 201, response.text
    sessions = SQLiteDebugSessionRepository(tmp_path / "outputs.db")
    runtime = OutputRuntime()
    backend = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    with TestClient(create_app(backend), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)
        response = client.post(PATH, json=PAYLOAD)
        assert response.status_code == 202, response.text
        yield client, backend, data, runtime, response.json(), entry
    sessions.close()


def output_path(session, node="inspect"):
    return PATH + "/" + session["session_id"] + "/steps/" + node + "/output"


def test_signed_output_redaction_pinned_ref_and_exact_run(outputs):
    client, backend, data, runtime, session, entry = outputs
    response = client.get(output_path(session))
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["values"] == {"status": "ready", "rows": [{"name": "first"}]}
    assert result["activity_ref"] == entry.reference.model_dump(mode="json")
    assert (
        result["node_id"] == "inspect"
        and result["definition_digest"] == session["definition_digest"]
    )
    assert set(result["redacted_paths"]) == {"", "/secret", "/unclassified", "/rows/0/password"}
    assert all(
        private not in response.text
        for private in (
            "output-secret",
            "unclassified-secret",
            "dynamic-private-key",
            "dynamic-secret",
            "password-secret",
        )
    )
    assert runtime.outputs[0][1:] == (session["execution_id"], "exact-run", "inspect")
    assert runtime.calls == []
    assert backend.activations.current(data[1], "hr") is None


def test_later_draft_and_catalog_lifecycle_do_not_change_pinned_output_schema(outputs):
    client, backend, data, _, session, entry = outputs
    original = client.get(output_path(session))
    assert original.status_code == 200, original.text
    changed = output_project(entry.reference)
    changed["workflows"][0]["output_schema"] = {
        "type": "object",
        "additionalProperties": True,
    }
    assert (
        client.put(
            BASE + "/projects/hr",
            json={"expected_revision": 1, "summary": "Edit", "content": changed},
        ).status_code
        == 200
    )
    backend.catalog.transition(data[1], entry.reference, "deprecated", 1, "publisher")
    backend.catalog.transition(data[1], entry.reference, "withdrawn", 2, "publisher")
    assert client.get(output_path(session)).json() == original.json()


def test_fresh_owner_and_revocation_guards_run_before_output_provider(outputs):
    client, _, data, runtime, session, _ = outputs
    client.headers["authorization"] = "Bearer " + data[4](sub="other-pairwise-subject")
    assert client.get(output_path(session)).status_code == 403
    assert runtime.outputs == []
    client.headers["authorization"] = "Bearer " + data[4]()
    assert client.get(output_path(session)).status_code == 200
    store, scope, grant, _, _ = data
    store.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(output_path(session)).status_code == 403
    assert len(runtime.outputs) == 1


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_non_dev_output_inspection_is_denied_before_provider(outputs, environment):
    client, _, data, runtime, session, _ = outputs
    store, scope, grant, _, _ = data
    target = scope.model_copy(update={"environment_id": environment})
    store.save_grant(
        replace(grant, scope=target, role="organization_administrator"), expected_revision=0
    )
    assert client.get(output_path(session).replace("/dev/", f"/{environment}/")).status_code == 403
    assert runtime.outputs == []


@pytest.mark.parametrize("node", ["start", "later", "missing"])
def test_non_activity_or_missing_nodes_do_not_query_raw_output(outputs, node):
    client, _, _, runtime, session, _ = outputs
    response = client.get(output_path(session, node))
    assert response.status_code in {404, 409}, response.text
    assert runtime.outputs == []


def test_uncompleted_output_returns_conflict(outputs):
    client, _, _, runtime, session, _ = outputs
    runtime.completed = False
    response = client.get(output_path(session))
    assert response.status_code == 409, response.text
    assert "output-secret" not in response.text


@pytest.mark.parametrize("mutation", ["runtime", "binding", "missing-schema", "changed-schema"])
def test_provider_and_schema_failures_are_closed_and_safe(outputs, mutation):
    client, backend, _, runtime, session, _ = outputs
    if mutation == "runtime":
        runtime.fail = True
    elif mutation == "binding":
        runtime.wrong_binding = True
    elif mutation == "missing-schema":
        backend.catalog._db.execute("DELETE FROM activity_catalog")
        backend.catalog._db.commit()
    else:
        row = backend.catalog._db.execute("SELECT manifest FROM activity_catalog").fetchone()
        manifest = ActivityManifest.model_validate_json(row[0]).model_dump(mode="json")
        manifest["descriptor"]["output_schema"]["properties"]["secret"].pop("x-secret")
        backend.catalog._db.execute(
            "UPDATE activity_catalog SET manifest=?", (json.dumps(manifest),)
        )
        backend.catalog._db.commit()
    response = client.get(output_path(session))
    assert response.status_code == 503, response.text
    assert "output-secret" not in response.text and "private-provider" not in response.text


@pytest.mark.parametrize(
    "raw", ['{"status":"one","status":"two"}', '{"status": NaN}', "[]", "private malformed output"]
)
def test_malformed_output_never_escapes(outputs, raw):
    client, _, _, runtime, session, _ = outputs
    runtime.raw = raw
    response = client.get(output_path(session))
    assert response.status_code == 503, response.text
    assert "private malformed" not in response.text


def test_invalid_node_identifier_is_strict_and_side_effect_free(outputs):
    client, _, _, runtime, session, _ = outputs
    response = client.get(output_path(session, "bad!node"))
    assert response.status_code == 422, response.text
    assert runtime.outputs == []


def test_pending_dispatch_rejects_output_inspection_before_runtime(outputs, monkeypatch):
    client, backend, data, runtime, _, _ = outputs

    async def unavailable(*args):
        raise OSError("private-start-detail")

    monkeypatch.setattr(runtime, "start", unavailable)
    payload = {**PAYLOAD, "operation_id": "pending-output"}
    assert client.post(PATH, json=payload).status_code == 503
    pending = backend.debug_sessions.operation(data[1], "hr", "pending-output")
    response = client.get(output_path(pending.session.model_dump(mode="json")))
    assert response.status_code == 409, response.text
    assert runtime.outputs == []


def test_public_output_scalar_and_array_bounds_remain_enforced(outputs):
    client, _, _, runtime, session, _ = outputs
    runtime.raw = json.dumps(
        {"status": "a" * 1025, "rows": [{"name": str(index)} for index in range(101)]}
    )
    response = client.get(output_path(session))
    assert response.status_code == 200, response.text
    result = response.json()
    assert "status" not in result["values"]
    assert len(result["values"]["rows"]) == 100
    assert result["truncated"] is True
    assert set(result["redacted_paths"]) == {"/status", "/rows"}
    assert len(response.content) <= 16384


def test_oversized_raw_output_fails_before_projection(outputs):
    client, _, _, runtime, session, _ = outputs
    runtime.raw = json.dumps({"status": "a" * 65536})
    response = client.get(output_path(session))
    assert response.status_code == 503, response.text
    assert len(response.content) < 1024
