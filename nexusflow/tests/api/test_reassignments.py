"""Signed admin assignment changes through real identity and task persistence."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.api.test_tasks import OTHER, Directory, tasks_api  # noqa: F401
from nexusflow.tests.identity.test_service import TENANT, USER, identity  # noqa: F401


def administrator(identity_data):
    repo, scope, _, _, _ = identity_data
    repo.save_grant(
        Grant("task-admin", scope, TENANT, "user", USER, "organization_administrator"),
        expected_revision=0,
    )


def test_signed_reassignment_changes_eligibility_and_preserves_retry(tasks_api):  # noqa: F811
    _, seed, identity_data, core, backend = tasks_api
    repo, scope, _, _, token = identity_data
    administrator(identity_data)
    task = seed()
    url = BASE + "/tasks/" + task.task_id
    payload = {"command_id": "move", "expected_revision": 1, "user_ids": [OTHER]}
    configured = replace(backend, task_assignments=Directory())
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers["authorization"] = "Bearer " + token()
        result = client.post(url + "/reassignments", json=payload)
        assert result.status_code == 200, result.text
        updated = result.json()
        assert updated["eligible_approver_ids"] == [OTHER]
        assert updated["revision"] == 2
        assert updated["assignment_history"][0]["actor_id"] == USER
        assert client.get(url).status_code == 403
        assert client.get(BASE + "/tasks").json()["items"] == []
        assert client.post(url + "/reassignments", json=payload).json() == updated
        assert (
            client.post(url + "/reassignments", json={**payload, "user_ids": [USER]}).status_code
            == 409
        )
        assert (
            client.post(
                url + "/decisions",
                json={"command_id": "old", "expected_revision": 2, "decision": "approve"},
            ).status_code
            == 403
        )
        repo.add_membership(scope.organization_id, TENANT, "user", OTHER)
        repo.save_grant(
            Grant("new-participant", scope, TENANT, "user", OTHER, "task_participant"),
            expected_revision=0,
        )
        client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-subject")
        audit = client.get(url + "/audit")
        assert audit.status_code == 200
        change = audit.json()[-1]
        assert change["kind"] == "reassigned"
        assert change["assignment"]["previous_eligible_ids"] == [USER]
        assert change["assignment"]["eligible_approver_ids"] == [OTHER]
        assert (
            client.post(
                url + "/decisions",
                json={"command_id": "approve-new", "expected_revision": 2, "decision": "approve"},
            ).json()["status"]
            == "approved"
        )
        client.headers["authorization"] = "Bearer " + token()
        assert client.post(url + "/reassignments", json=payload).json() == updated
        repo.revoke_grant(scope, "task-admin", expected_revision=1)
        assert client.post(url + "/reassignments", json=payload).status_code == 403
    assert len(core.pending_completions(scope)) == 1


def test_participant_cannot_reassign_and_admin_requires_resolver(tasks_api):  # noqa: F811
    client, seed, identity_data, _, _ = tasks_api
    task = seed()
    url = BASE + "/tasks/" + task.task_id + "/reassignments"
    payload = {"command_id": "move", "expected_revision": 1, "user_ids": [OTHER]}
    assert client.post(url, json=payload).status_code == 403
    administrator(identity_data)
    result = client.post(url, json=payload)
    assert result.status_code == 503
    assert result.json()["code"] == "unavailable"
    assert "resolver" not in result.text
    assert client.get(BASE + "/tasks/" + task.task_id).json()["revision"] == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"user_ids": [], "group_ids": []},
        {"expected_revision": 0},
        {"actor_id": OTHER},
        {"user_ids": ["invalid/user"]},
    ],
)
def test_reassignment_rejects_invalid_http_commands(tasks_api, extra):  # noqa: F811
    client, seed, _, _, _ = tasks_api
    task = seed()
    payload = {"command_id": "move", "expected_revision": 1, "user_ids": [OTHER], **extra}
    result = client.post(BASE + "/tasks/" + task.task_id + "/reassignments", json=payload)
    assert result.status_code == 422
    assert OTHER not in result.text
