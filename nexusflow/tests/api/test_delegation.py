"""Verified participants transfer their own uncast vote without granting roles."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.api.test_tasks import OTHER, Directory, tasks_api  # noqa: F401
from nexusflow.tests.identity.test_service import TENANT, USER, identity  # noqa: F401


def test_signed_delegation_removes_access_but_does_not_grant_target_roles(tasks_api):  # noqa: F811
    _, seed, identity_data, core, backend = tasks_api
    repo, scope, _, _, token = identity_data
    task = seed()
    url = BASE + "/tasks/" + task.task_id
    payload = {"command_id": "delegate-vote", "expected_revision": 1, "delegate_id": OTHER}
    with TestClient(create_app(replace(backend, task_assignments=Directory()))) as client:
        client.headers["authorization"] = "Bearer " + token()
        result = client.post(url + "/delegations", json=payload)
        assert result.status_code == 200, result.text
        delegated = result.json()
        assert delegated["eligible_approver_ids"] == [OTHER]
        assert delegated["delegation_history"][0]["actor_id"] == USER
        assert delegated["revision"] == 2
        assert client.get(url).status_code == 403
        assert client.get(BASE + "/tasks").json()["items"] == []
        assert client.post(url + "/delegations", json=payload).json() == delegated
        assert (
            client.post(
                url + "/decisions",
                json={"command_id": "old-vote", "expected_revision": 2, "decision": "approve"},
            ).status_code
            == 403
        )
        client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-subject")
        assert client.get(url).status_code == 403
        repo.add_membership(scope.organization_id, TENANT, "user", OTHER)
        repo.save_grant(
            Grant("delegate-participant", scope, TENANT, "user", OTHER, "task_participant"),
            expected_revision=0,
        )
        assert client.get(url).status_code == 200
        audit = client.get(url + "/audit").json()
        assert audit[-1]["kind"] == "delegated"
        assert audit[-1]["delegation"]["delegate_id"] == OTHER
        final = client.post(
            url + "/decisions",
            json={"command_id": "new-vote", "expected_revision": 2, "decision": "approve"},
        )
        assert final.status_code == 200 and final.json()["status"] == "approved"
        client.headers["authorization"] = "Bearer " + token()
        assert client.post(url + "/delegations", json=payload).json() == delegated
        repo.revoke_grant(scope, "participant", expected_revision=1)
        assert client.post(url + "/delegations", json=payload).status_code == 403
    assert len(core.pending_completions(scope)) == 1


def test_delegation_requires_current_assignment_and_configured_resolver(tasks_api):  # noqa: F811
    client, seed, _, _, _ = tasks_api
    hidden = seed(run="hidden", users=(OTHER,))
    current = seed()
    payload = {"command_id": "delegate", "expected_revision": 1, "delegate_id": OTHER}
    assert (
        client.post(BASE + "/tasks/" + hidden.task_id + "/delegations", json=payload).status_code
        == 403
    )
    unavailable = client.post(BASE + "/tasks/" + current.task_id + "/delegations", json=payload)
    assert unavailable.status_code == 503
    assert "resolver" not in unavailable.text
    assert client.get(BASE + "/tasks/" + current.task_id).json()["revision"] == 1


@pytest.mark.parametrize(
    "override", [{"expected_revision": 0}, {"actor_id": USER}, {"delegate_id": "bad/id"}]
)
def test_delegation_transport_rejects_forged_and_invalid_fields(tasks_api, override):  # noqa: F811
    client, seed, _, _, _ = tasks_api
    task = seed()
    payload = {"command_id": "delegate", "expected_revision": 1, "delegate_id": OTHER, **override}
    result = client.post(BASE + "/tasks/" + task.task_id + "/delegations", json=payload)
    assert result.status_code == 422
    assert OTHER not in result.text
