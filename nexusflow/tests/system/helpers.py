"""Thin helpers composing local launcher, template and package APIs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID
from nexusflow.contracts import Scope
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_template_acceptance import request_body
from nexusflow.tests.runtime.test_local_launcher_integration import _cli, _ports, _run, _serve
from nexusflow.tests.runtime.test_local_tasks_integration import _initialized

STARTERS = {
    "employee-onboarding": {
        "project_id": "onboarding-ref",
        "inputs": {
            "employee_name": "Ada Reference",
            "department": "Engineering",
            "start_date": "2026-11-01",
        },
        "steps": ("manager", "hr", "it"),
    },
    "purchase-requisition": {
        "project_id": "purchase-ref",
        "inputs": {
            "requester": "Grace Reference",
            "item": "Laptop",
            "amount": 1200,
            "currency": "USD",
        },
        "steps": ("manager", "finance"),
    },
}


def grant_author(state: Path, organization: str, environment: str, grant_id: str) -> None:
    scope = Scope(organization_id=organization, environment_id=environment)
    role = "production_support" if environment == "prod" else "business_author"
    repository = SQLiteIdentityRepository(state / "identity.db")
    try:
        repository.save_grant(
            Grant(grant_id, scope, TENANT_ID, "user", USER_ID, role),
            expected_revision=0,
        )
    finally:
        repository.close()


def env_base(organization: str, environment: str) -> str:
    return f"/api/v1/organizations/{organization}/environments/{environment}"


def create_publish_activate(
    client: httpx.Client,
    *,
    organization: str,
    template: str,
    headers: dict[str, str],
    project_id: str | None = None,
) -> tuple[str, str, str]:
    starter = STARTERS[template]
    project = project_id or starter["project_id"]
    body = request_body(template, project)
    for assignment in body["assignments"].values():
        assignment["user_ids"] = [USER_ID]
    base = env_base(organization, "dev")
    created = client.post(
        base + f"/project-templates/{template}/projects", headers=headers, json=body
    )
    assert created.status_code == 201, created.text
    workflow = created.json()["content"]["workflows"][0]["workflow_id"]
    project_path = base + "/projects/" + project
    release = client.post(
        project_path + "/releases",
        headers=headers,
        json={"source_revision": 1, "idempotency_key": f"{project}-release"},
    )
    assert release.status_code == 201, release.text
    release_id = release.json()["release_id"]
    activated = client.post(
        project_path + "/activation",
        headers=headers,
        json={
            "project_id": project,
            "release_id": release_id,
            "operation_id": f"{project}-activate",
            "configuration_revision": "local-v1",
            "expected_revision": 0,
        },
    )
    assert activated.status_code == 200, activated.text
    return project_path, workflow, release_id


def start_run(
    client: httpx.Client,
    project_path: str,
    headers: dict[str, str],
    workflow: str,
    inputs: dict,
    key: str,
) -> str:
    accepted = client.post(
        project_path + "/executions",
        headers=headers,
        json={"workflow_id": workflow, "idempotency_key": key, "input": inputs},
    )
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["execution_id"]


def _transient_unavailable(response: httpx.Response) -> bool:
    if response.status_code == 503:
        return True
    if response.status_code < 500:
        return False
    try:
        return response.json().get("code") == "unavailable"
    except Exception:
        return False


def pending_task(client: httpx.Client, base: str, headers: dict[str, str], execution: str) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        response = client.get(base + "/tasks", headers=headers, params={"run_id": execution})
        if _transient_unavailable(response):
            time.sleep(0.2)
            continue
        assert response.status_code == 200, response.text
        tasks = [task for task in response.json()["items"] if task["status"] == "pending"]
        assert len(tasks) <= 1, "Linear starter created concurrent approval tasks"
        if tasks:
            assert tasks[0]["eligible_approver_ids"] == [USER_ID]
            return tasks[0]
        time.sleep(0.1)
    raise AssertionError("Starter did not create its next pending task")


def approve_task(
    client: httpx.Client,
    base: str,
    headers: dict[str, str],
    task: dict,
    inputs: dict,
) -> None:
    url = base + "/tasks/" + task["task_id"]
    preview = client.get(url + "/form", headers=headers)
    assert preview.status_code == 200, preview.text
    values = json.loads(preview.json()["values_json"])
    for key, value in inputs.items():
        assert values[key] == value
    submitted = client.post(
        url + "/form",
        headers=headers,
        json={
            "expected_revision": task["revision"],
            "values": {"confirmed": True, "notes": "System acceptance"},
        },
    )
    assert submitted.status_code == 200, submitted.text
    decision = client.post(
        url + "/decisions",
        headers=headers,
        json={
            "command_id": "decision-" + task["task_id"],
            "expected_revision": submitted.json()["revision"],
            "decision": "approve",
        },
    )
    assert decision.status_code == 200, decision.text


def wait_completed(
    client: httpx.Client, project_path: str, headers: dict[str, str], execution: str
) -> dict:
    deadline = time.monotonic() + 45
    last: dict | str | None = None
    while time.monotonic() < deadline:
        result = client.get(project_path + "/executions/" + execution, headers=headers)
        if _transient_unavailable(result):
            last = f"unavailable:{result.status_code}:{result.text[:200]}"
            time.sleep(0.2)
            continue
        assert result.status_code == 200, result.text
        payload = result.json()
        last = payload
        if payload["state"] in ("completed", "failed"):
            return payload
        time.sleep(0.1)
    raise AssertionError(f"Starter did not finish after approvals; last={last!r}")


def complete_starter_run(
    client: httpx.Client,
    *,
    organization: str,
    template: str,
    headers: dict[str, str],
    start_key: str,
) -> tuple[str, str, str]:
    starter = STARTERS[template]
    base = env_base(organization, "dev")
    project_path, workflow, release_id = create_publish_activate(
        client, organization=organization, template=template, headers=headers
    )
    execution = start_run(client, project_path, headers, workflow, starter["inputs"], start_key)
    for step in starter["steps"]:
        task = pending_task(client, base, headers, execution)
        assert task["request"]["node_invocation_id"] == step
        approve_task(client, base, headers, task, starter["inputs"])
    status = wait_completed(client, project_path, headers, execution)
    assert status["state"] == "completed", status
    tasks = client.get(base + "/tasks", headers=headers, params={"run_id": execution}).json()[
        "items"
    ]
    assert len(tasks) == len(starter["steps"])
    assert {task["request"]["node_invocation_id"] for task in tasks} == set(starter["steps"])
    assert not any(task["status"] == "pending" for task in tasks)
    return project_path, release_id, execution


def bump_draft_revision(
    client: httpx.Client,
    project_path: str,
    headers: dict[str, str],
    *,
    expected_revision: int,
    summary: str,
) -> int:
    """Save a non-breaking draft change so a second release can be published."""
    current = client.get(project_path, headers=headers)
    assert current.status_code == 200, current.text
    content = current.json()["content"]
    touched = False
    for workflow in content["workflows"]:
        for node in workflow["nodes"]:
            human_task = node.get("input_mapping", {}).get("human_task")
            if not isinstance(human_task, dict):
                continue
            form = human_task.get("form")
            if not isinstance(form, dict):
                continue
            definition = form.get("definition")
            if not isinstance(definition, dict):
                continue
            schema = definition.get("data_schema", {})
            properties = schema.get("properties", {})
            notes = properties.get("notes")
            if isinstance(notes, dict) and isinstance(notes.get("maxLength"), int):
                notes["maxLength"] = max(1, notes["maxLength"] - 1)
                touched = True
                break
        if touched:
            break
    assert touched, "Starter content lacked an editable approval form notes bound"
    saved = client.put(
        project_path,
        headers=headers,
        json={
            "expected_revision": expected_revision,
            "summary": summary,
            "content": content,
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["revision"]


def publish_release(
    client: httpx.Client,
    project_path: str,
    headers: dict[str, str],
    *,
    source_revision: int,
    idempotency_key: str,
) -> str:
    release = client.post(
        project_path + "/releases",
        headers=headers,
        json={"source_revision": source_revision, "idempotency_key": idempotency_key},
    )
    assert release.status_code == 201, release.text
    return release.json()["release_id"]


def activate_release(
    client: httpx.Client,
    project_path: str,
    headers: dict[str, str],
    *,
    project_id: str,
    release_id: str,
    operation_id: str,
    expected_revision: int,
    action: str = "activate",
) -> dict:
    response = client.post(
        project_path + "/activation",
        headers=headers,
        json={
            "project_id": project_id,
            "release_id": release_id,
            "operation_id": operation_id,
            "configuration_revision": "local-v1",
            "expected_revision": expected_revision,
            "action": action,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def finish_remaining_approvals(
    client: httpx.Client,
    *,
    organization: str,
    template: str,
    headers: dict[str, str],
    project_path: str,
    execution: str,
    remaining_steps: tuple[str, ...],
) -> dict:
    starter = STARTERS[template]
    base = env_base(organization, "dev")
    for step in remaining_steps:
        task = pending_task(client, base, headers, execution)
        assert task["request"]["node_invocation_id"] == step
        approve_task(client, base, headers, task, starter["inputs"])
        # Allow async completion delivery to Temporal before the next poll/create.
        time.sleep(0.2)
    status = wait_completed(client, project_path, headers, execution)
    assert status["state"] == "completed", status
    return status


__all__ = [
    "STARTERS",
    "_cli",
    "_initialized",
    "_ports",
    "_run",
    "_serve",
    "activate_release",
    "approve_task",
    "bump_draft_revision",
    "complete_starter_run",
    "create_publish_activate",
    "env_base",
    "finish_remaining_approvals",
    "grant_author",
    "pending_task",
    "publish_release",
    "start_run",
    "wait_completed",
]
