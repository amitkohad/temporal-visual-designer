"""Actual launcher/HTTP/Temporal acceptance for release-pinned managed forms."""

import copy
import json
import time

import pytest

from nexusflow.apps.local_bootstrap import USER_ID
from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.tests.runtime.test_local_launcher_integration import ROOT, _cli, _ports, _serve
from nexusflow.tests.runtime.test_local_tasks_integration import _initialized, _wait_task

pytestmark = pytest.mark.integration


def _publish_form_project(client, base, headers):
    payload = json.loads((ROOT / "examples/local-approval-form-project.json").read_text("utf-8"))
    response = client.post(base + "/projects", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    project = base + "/projects/" + payload["project_id"]
    response = client.post(
        project + "/releases",
        headers=headers,
        json={"source_revision": 1, "idempotency_key": "forms-release"},
    )
    assert response.status_code == 201, response.text
    release = response.json()
    response = client.post(
        project + "/activation",
        headers=headers,
        json={
            "project_id": payload["project_id"],
            "release_id": release["release_id"],
            "operation_id": "forms-activate",
            "configuration_revision": "local-v1",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    return project, payload, release


def test_managed_form_is_atomic_pinned_and_survives_restart_until_real_completion(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    base = f"/api/v1/organizations/{organization}/environments/dev"
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        project, payload, release = _publish_form_project(client, base, headers)
        workflow = payload["content"]["workflows"][0]
        node = next(node for node in workflow["nodes"] if node["kind"] == "approval")
        pinned = node["input_mapping"]["human_task"]["form"]
        initial = pinned["initial_values"]
        properties = pinned["definition"]["data_schema"]["properties"]
        read_only = next(key for key, schema in properties.items() if schema.get("readOnly"))
        write_only = next(key for key, schema in properties.items() if schema.get("writeOnly"))
        start = {"workflow_id": workflow["workflow_id"], "idempotency_key": "form-run", "input": {}}
        response = client.post(project + "/executions", headers=headers, json=start)
        assert response.status_code == 202, response.text
        accepted = response.json()
        task = _wait_task(client, base, headers, accepted["execution_id"])
        assert task["revision"] == 2
        assert task["eligible_approver_ids"] == [USER_ID]
        task_url = base + "/tasks/" + task["task_id"]
        form_url = task_url + "/form"
        preview = client.get(form_url, headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["revision"] == 2
        values = json.loads(preview.json()["values_json"])
        assert values["department"] == "Engineering"
        assert values[read_only] == initial[read_only]
        assert write_only not in values
        fields = {field["field_id"]: field for field in preview.json()["fields"]}
        assert fields["employee"]["required"] and fields["employee"]["editable"]
        assert not fields[read_only]["editable"]
        assert fields[write_only]["write_only"]
        decision = {"command_id": "form-approve", "expected_revision": 2, "decision": "approve"}
        assert (
            client.post(task_url + "/decisions", headers=headers, json=decision).status_code == 409
        )
        for patch, status in [({"employee": ""}, 422), ({read_only: "changed"}, 403)]:
            response = client.post(
                form_url, headers=headers, json={"expected_revision": 2, "values": patch}
            )
            assert response.status_code == status, response.text
            assert client.get(task_url, headers=headers).json()["revision"] == 2
        response = client.post(
            form_url,
            headers=headers,
            json={
                "expected_revision": 2,
                "values": {"employee": "Ada", write_only: "private-token"},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 3
        saved_preview = client.get(form_url, headers=headers).json()
        assert json.loads(saved_preview["values_json"])["employee"] == "Ada"
        assert "private-token" not in json.dumps(saved_preview)
        # Editing a later draft cannot alter the release-bound task definition or values.
        changed = copy.deepcopy(payload["content"])
        changed_node = next(
            node for node in changed["workflows"][0]["nodes"] if node["kind"] == "approval"
        )
        changed_node["input_mapping"]["human_task"]["form"]["initial_values"]["department"] = (
            "Changed"
        )
        changed_node["input_mapping"]["human_task"]["form"]["definition"]["data_schema"][
            "properties"
        ]["employee"]["minLength"] = 40
        response = client.put(
            project,
            headers=headers,
            json={"expected_revision": 1, "summary": "Different draft form", "content": changed},
        )
        assert response.status_code == 200, response.text
        assert client.get(form_url, headers=headers).json() == saved_preview
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(form_url, headers=headers).json() == saved_preview
        assert client.get(task_url, headers=headers).json()["revision"] == 3
        assert (
            client.get(project + "/activation", headers=headers).json()["release_id"]
            == release["release_id"]
        )
        decision["expected_revision"] = 3
        response = client.post(task_url + "/decisions", headers=headers, json=decision)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 4
        execution_url = project + "/executions/" + accepted["execution_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(execution_url, headers=headers)
            assert response.status_code == 200, response.text
            if response.json()["state"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Form-gated approval did not resume the actual Temporal workflow")
        assert response.json()["completed_nodes"] == ["start", "approval", "end"]
        assert all(
            not field["editable"]
            for field in client.get(form_url, headers=headers).json()["fields"]
        )
        assert (
            client.post(
                form_url,
                headers=headers,
                json={"expected_revision": 4, "values": {"employee": "Grace"}},
            ).status_code
            == 409
        )
    identity = SQLiteIdentityRepository(state / "identity.db")
    try:
        identity.revoke_grant(
            Scope(organization_id=organization, environment_id="dev"),
            "local-task-participant",
            expected_revision=1,
        )
    finally:
        identity.close()
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(form_url, headers=headers).status_code == 403
        assert (
            client.post(
                form_url, headers=headers, json={"expected_revision": 4, "values": {}}
            ).status_code
            == 403
        )
    logs = log.read_text("utf-8", errors="replace")
    assert "private-token" not in logs
    assert headers["Authorization"].split(" ")[1] not in logs
