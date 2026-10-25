"""Business starters through signed socket APIs, real Temporal and restart."""

import json
import time

import pytest

from nexusflow.apps.local_bootstrap import USER_ID
from nexusflow.tests.api.test_template_acceptance import request_body
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _serve
from nexusflow.tests.runtime.test_local_tasks_integration import _initialized

pytestmark = pytest.mark.integration


def pending(client, base, headers, execution):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(base + "/tasks", headers=headers, params={"run_id": execution})
        assert response.status_code == 200, response.text
        tasks = [task for task in response.json()["items"] if task["status"] == "pending"]
        assert len(tasks) <= 1, "Linear starter created concurrent approval tasks"
        if tasks:
            assert tasks[0]["eligible_approver_ids"] == [USER_ID]
            return tasks[0]
        time.sleep(0.1)
    pytest.fail("Starter did not create its next pending task")


def complete(client, base, headers, task, inputs, *, reject=False):
    url = base + "/tasks/" + task["task_id"]
    preview = client.get(url + "/form", headers=headers)
    assert preview.status_code == 200, preview.text
    values = json.loads(preview.json()["values_json"])
    for key, value in inputs.items():
        assert values[key] == value
    revision = task["revision"]
    decision = {
        "command_id": "decision-" + task["task_id"],
        "expected_revision": revision,
        "decision": "approve",
    }
    assert client.post(url + "/decisions", headers=headers, json=decision).status_code == 409
    if reject:
        decision["decision"] = "reject"
        rejected = client.post(url + "/decisions", headers=headers, json=decision)
        assert rejected.status_code == 200, rejected.text
        return
    first_key = next(iter(inputs))
    forged = {"expected_revision": revision, "values": {first_key: "forged", "confirmed": True}}
    assert client.post(url + "/form", headers=headers, json=forged).status_code == 403
    assert (
        client.post(
            url + "/form",
            headers=headers,
            json={
                "expected_revision": revision,
                "values": {"confirmed": False},
            },
        ).status_code
        == 422
    )
    submitted = client.post(
        url + "/form",
        headers=headers,
        json={
            "expected_revision": revision,
            "values": {"confirmed": True, "notes": "Reviewed"},
        },
    )
    assert submitted.status_code == 200, submitted.text
    decision["expected_revision"] = submitted.json()["revision"]
    result = client.post(url + "/decisions", headers=headers, json=decision)
    assert result.status_code == 200, result.text


@pytest.mark.parametrize(
    "template,inputs,steps",
    [
        (
            "employee-onboarding",
            {
                "employee_name": "Ada Private",
                "department": "Engineering",
                "start_date": "2026-11-01",
            },
            3,
        ),
        (
            "purchase-requisition",
            {"requester": "Grace Private", "item": "Laptop", "amount": 1200, "currency": "USD"},
            1,
        ),
    ],
)
def test_starter_forms_execute_with_background_delivery_and_restart(
    tmp_path, template, inputs, steps
):
    executable = _cli()
    state = tmp_path / "state"
    organization, headers = _initialized(state)
    base = f"/api/v1/organizations/{organization}/environments/dev"
    api_port, temporal_port = _ports()
    log = tmp_path / "launcher.log"
    body = request_body(template)
    for assignment in body["assignments"].values():
        assignment["user_ids"] = [USER_ID]
    project = base + "/projects/starter"
    with _serve(state, executable, api_port, temporal_port, log) as client:
        created = client.post(
            base + f"/project-templates/{template}/projects", headers=headers, json=body
        )
        assert created.status_code == 201, created.text
        workflow = created.json()["content"]["workflows"][0]["workflow_id"]
        release = client.post(
            project + "/releases",
            headers=headers,
            json={
                "source_revision": 1,
                "idempotency_key": "starter-release",
            },
        )
        assert release.status_code == 201, release.text
        activated = client.post(
            project + "/activation",
            headers=headers,
            json={
                "project_id": "starter",
                "release_id": release.json()["release_id"],
                "operation_id": "starter-activate",
                "configuration_revision": "local-v1",
                "expected_revision": 0,
            },
        )
        assert activated.status_code == 200, activated.text
        start = {"workflow_id": workflow, "idempotency_key": "starter-start", "input": inputs}
        accepted = client.post(project + "/executions", headers=headers, json=start)
        assert accepted.status_code == 202, accepted.text
        execution = accepted.json()["execution_id"]
        task = pending(client, base, headers, execution)
        assert task["request"]["node_invocation_id"] == "manager"
        if steps > 1:
            complete(client, base, headers, task, inputs)
            task = pending(client, base, headers, execution)
            assert task["request"]["node_invocation_id"] == "hr"
        before = client.get(base + "/tasks/" + task["task_id"] + "/form", headers=headers).json()
    with _serve(state, executable, api_port, temporal_port, log) as client:
        assert pending(client, base, headers, execution)["task_id"] == task["task_id"]
        assert (
            client.get(base + "/tasks/" + task["task_id"] + "/form", headers=headers).json()
            == before
        )
        complete(client, base, headers, task, inputs, reject=steps == 1)
        if steps > 1:
            task = pending(client, base, headers, execution)
            assert task["request"]["node_invocation_id"] == "it"
            complete(client, base, headers, task, inputs)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = client.get(project + "/executions/" + execution, headers=headers)
            assert result.status_code == 200, result.text
            if result.json()["state"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        else:
            pytest.fail("Starter did not finish after task delivery")
        assert result.json()["state"] == ("failed" if steps == 1 else "completed"), result.text
        tasks = client.get(base + "/tasks", headers=headers, params={"run_id": execution}).json()[
            "items"
        ]
        assert len(tasks) == steps
        assert {task["request"]["node_invocation_id"] for task in tasks} == (
            {"manager"} if steps == 1 else {"manager", "hr", "it"}
        )
        assert not any(task["status"] == "pending" for task in tasks)
    logs = log.read_text("utf-8", errors="replace")
    assert headers["Authorization"].split(" ")[1] not in logs
    assert "Private" not in logs
