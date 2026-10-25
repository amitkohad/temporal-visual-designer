"""Real launcher acceptance for input-bound managed approval forms."""

import copy
import json
import time

import pytest

from nexusflow.tests.runtime.test_local_launcher_integration import ROOT, _cli, _ports, _serve
from nexusflow.tests.runtime.test_local_tasks_integration import _initialized, _wait_task

pytestmark = pytest.mark.integration


def test_input_bound_forms_pin_each_run_and_resume_after_restart(tmp_path):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    base = f"/api/v1/organizations/{organization}/environments/dev"
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    payload = json.loads((ROOT / "examples/local-approval-input-project.json").read_text("utf-8"))
    project = base + "/projects/" + payload["project_id"]
    workflow = payload["content"]["workflows"][0]
    start = {
        "workflow_id": workflow["workflow_id"],
        "idempotency_key": "input-first",
        "input": {"employee": {"name": "Ada private input"}, "department": "Engineering"},
    }
    with _serve(state, executable, api_port, temporal_port, log) as client:
        response = client.post(base + "/projects", headers=headers, json=payload)
        assert response.status_code == 201, response.text
        response = client.post(
            project + "/releases",
            headers=headers,
            json={"source_revision": 1, "idempotency_key": "inputs-release"},
        )
        assert response.status_code == 201, response.text
        release_id = response.json()["release_id"]
        response = client.post(
            project + "/activation",
            headers=headers,
            json={
                "project_id": payload["project_id"],
                "release_id": release_id,
                "operation_id": "inputs-activate",
                "configuration_revision": "local-v1",
                "expected_revision": 0,
            },
        )
        assert response.status_code == 200, response.text
        # Publication and activation must not attempt to resolve absent runtime inputs.
        invalid = copy.deepcopy(start)
        invalid["idempotency_key"] = "invalid-input"
        invalid["input"] = {"employee": {"name": ""}, "department": "invalid-private"}
        response = client.post(project + "/executions", headers=headers, json=invalid)
        assert response.status_code == 422, response.text
        assert "invalid-private" not in response.text
        response = client.post(project + "/executions", headers=headers, json=start)
        assert response.status_code == 202, response.text
        accepted = response.json()
        first_task = _wait_task(client, base, headers, accepted["execution_id"])
        first_url = base + "/tasks/" + first_task["task_id"]
        first_preview = client.get(first_url + "/form", headers=headers).json()
        values = json.loads(first_preview["values_json"])
        assert values == {
            "employee": "Ada private input",
            "department": "Engineering",
            "reference": "HR-LOCAL",
        }
        assert first_preview["revision"] == 2
        changed_start = copy.deepcopy(start)
        changed_start["input"]["employee"]["name"] = "Grace private input"
        response = client.post(project + "/executions", headers=headers, json=changed_start)
        assert response.status_code == 409, response.text
        changed_start["idempotency_key"] = "input-second"
        response = client.post(project + "/executions", headers=headers, json=changed_start)
        assert response.status_code == 202, response.text
        second = response.json()
        second_task = _wait_task(client, base, headers, second["execution_id"])
        second_url = base + "/tasks/" + second_task["task_id"]
        assert second_task["task_id"] != first_task["task_id"]
        second_preview = client.get(second_url + "/form", headers=headers).json()
        assert json.loads(second_preview["values_json"])["employee"] == "Grace private input"
        decision = {"command_id": "input-approve", "expected_revision": 2, "decision": "approve"}
        response = client.post(first_url + "/decisions", headers=headers, json=decision)
        assert response.status_code == 409, response.text
        changed_content = copy.deepcopy(payload["content"])
        node = next(n for n in changed_content["workflows"][0]["nodes"] if n["kind"] == "approval")
        node["input_mapping"]["human_task"]["form"]["initial_values"]["employee"] = "Draft edit"
        response = client.put(
            project,
            headers=headers,
            json={
                "expected_revision": 1,
                "summary": "Change draft mapping",
                "content": changed_content,
            },
        )
        assert response.status_code == 200, response.text
        assert client.get(first_url + "/form", headers=headers).json() == first_preview
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert client.get(first_url + "/form", headers=headers).json() == first_preview
        assert client.get(second_url + "/form", headers=headers).json() == second_preview
        response = client.post(project + "/executions", headers=headers, json=start)
        assert response.status_code == 202, response.text
        assert response.json() == accepted
        # Valid initial data is still unsubmitted; an explicit validation is required.
        response = client.post(
            first_url + "/form", headers=headers, json={"expected_revision": 2, "values": {}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 3
        decision["expected_revision"] = 3
        response = client.post(first_url + "/decisions", headers=headers, json=decision)
        assert response.status_code == 200, response.text
        execution_url = project + "/executions/" + accepted["execution_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(execution_url, headers=headers)
            assert response.status_code == 200, response.text
            if response.json()["state"] == "completed":
                break
            time.sleep(0.1)
        else:
            pytest.fail("Input-bound approval did not resume actual Temporal execution")
        assert response.json()["completed_nodes"] == ["start", "approval", "end"]
        assert client.get(second_url, headers=headers).json()["status"] == "pending"
        assert client.get(second_url + "/form", headers=headers).json() == second_preview
        for path in (execution_url, first_url, first_url + "/audit"):
            exposed = client.get(path, headers=headers)
            assert exposed.status_code == 200, exposed.text
            assert "Ada private input" not in exposed.text
            assert "Grace private input" not in exposed.text
            assert "demo-hidden-value" not in exposed.text
    logs = log.read_text("utf-8", errors="replace")
    assert "Ada private input" not in logs
    assert "Grace private input" not in logs
    assert "invalid-private" not in logs
    assert headers["Authorization"].split(" ")[1] not in logs
