"""Signed identities and durable SQLite human tasks across the HTTP boundary."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import CreateTask
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.identity.test_service import GROUP, TENANT, USER, Groups, identity  # noqa: F401

OTHER = "55555555-5555-4555-8555-555555555555"


class TrustedWorker:
    def authorize(self, scope, permission, resource_id):
        assert permission == "task.create"
        return "trusted-worker"


class Directory:
    def resolve(self, scope, user_ids, group_ids):
        return tuple(sorted(set(user_ids) | ({USER} if GROUP in group_ids else set())))


@pytest.fixture
def tasks_api(api, tmp_path):  # noqa: F811
    _, backend, identity_data, _ = api
    repository, scope, _, _, token = identity_data
    grant = Grant("participant", scope, TENANT, "user", USER, "task_participant")
    repository.save_grant(grant, expected_revision=0)
    path = tmp_path / "tasks.db"
    service = TaskService(path, TrustedWorker(), Directory())

    def seed(run="run-a", users=(USER,), groups=(), invocation="approval", placement=scope):
        return service.create(
            placement,
            CreateTask(
                run_id=run,
                node_invocation_id=invocation,
                definition_revision="r1",
                rule="any_one",
                user_ids=users,
                group_ids=groups,
            ),
        )

    configured = replace(backend, tasks_database=path)
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers["authorization"] = "Bearer " + token()
        yield client, seed, identity_data, service, configured
    service.close()


def test_inbox_filters_pagination_and_signed_oid(tasks_api):
    client, seed, _, _, _ = tasks_api
    direct = seed()
    group = seed(run="run-b", users=(), groups=(GROUP,))
    hidden = seed(run="private", users=(OTHER,))
    response = client.get(BASE + "/tasks", params={"limit": 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page["items"]) == 1 and page["next_offset"] == 1
    next_page = client.get(BASE + "/tasks", params={"limit": 1, "offset": 1}).json()
    assert next_page["next_offset"] is None
    assert {page["items"][0]["task_id"], next_page["items"][0]["task_id"]} == {
        direct.task_id,
        group.task_id,
    }
    filtered = client.get(BASE + "/tasks", params={"run_id": "run-b", "status": "pending"})
    assert [item["task_id"] for item in filtered.json()["items"]] == [group.task_id]
    assert client.get(BASE + "/tasks/" + hidden.task_id).status_code == 403
    assert client.get(BASE + "/tasks/" + hidden.task_id + "/audit").status_code == 403
    assert client.get(BASE + "/tasks/" + direct.task_id).json()["eligible_approver_ids"] == [USER]


def test_decision_retry_conflict_and_durable_audit(tasks_api):
    client, seed, _, service, _ = tasks_api
    task = seed()
    url = BASE + "/tasks/" + task.task_id
    payload = {"command_id": "decide-one", "expected_revision": 1, "decision": "approve"}
    result = client.post(url + "/decisions", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "approved"
    assert result.json()["decisions"][0]["actor_id"] == USER
    assert client.post(url + "/decisions", json=payload).json() == result.json()
    assert (
        client.post(url + "/decisions", json={**payload, "decision": "reject"}).status_code == 409
    )
    assert (
        client.post(url + "/decisions", json={**payload, "command_id": "stale"}).status_code == 409
    )
    audit = client.get(url + "/audit")
    assert [event["kind"] for event in audit.json()] == ["created", "decided"]
    assert len(service.pending_completions(task.scope)) == 1
    assert client.get(BASE + "/tasks", params={"status": "pending"}).json()["items"] == []


def test_scope_revocation_and_missing_bearer(tasks_api):
    client, seed, identity_data, _, _ = tasks_api
    task = seed()
    repo, scope, _, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(
        Grant("participant", uat, TENANT, "user", USER, "task_participant"), expected_revision=0
    )
    other = BASE.replace("/dev", "/uat")
    assert client.get(other + "/tasks").json()["items"] == []
    assert client.get(other + "/tasks/" + task.task_id).status_code == 404
    assert client.get(BASE.replace("/acme/", "/foreign/") + "/tasks").status_code == 401
    repo.revoke_grant(scope, "participant", expected_revision=1)
    assert client.get(BASE + "/tasks").status_code == 403
    assert client.get(BASE + "/tasks/" + task.task_id).status_code == 403
    assert (
        client.post(
            BASE + "/tasks/" + task.task_id + "/decisions",
            json={
                "command_id": "revoked",
                "expected_revision": 1,
                "decision": "approve",
            },
        ).status_code
        == 403
    )
    del client.headers["authorization"]
    assert client.get(BASE + "/tasks").status_code == 401


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "offset=-1", "offset=10001", "status=secret", "run_id=bad%20id"],
)
def test_query_validation(tasks_api, query):
    client, _, _, _, _ = tasks_api
    response = client.get(BASE + "/tasks?" + query)
    assert response.status_code == 422, response.text
    assert "secret" not in response.text


def test_task_body_boundary_and_missing_routes(tasks_api):
    client, seed, _, _, _ = tasks_api
    task = seed()
    url = BASE + "/tasks/" + task.task_id
    for payload in (
        {"command_id": "one", "expected_revision": 0, "decision": "approve"},
        {"command_id": "one", "expected_revision": 1, "decision": "approve", "actor_id": OTHER},
    ):
        assert client.post(url + "/decisions", json=payload).status_code == 422
    assert client.get(BASE + "/tasks/missing").status_code == 404
    assert client.get(BASE + "/tasks/missing/audit").status_code == 404
    assert client.post(BASE + "/tasks", json={}).status_code == 405
    assert client.post(url + "/deliver", json={}).status_code == 404


def test_disabled_provider_is_safe_unavailable(api):  # noqa: F811
    client, _, _, _ = api
    for suffix in ("/tasks", "/tasks/missing", "/tasks/missing/audit"):
        response = client.get(BASE + suffix)
        assert response.status_code == 503
        assert response.json()["code"] == "unavailable"
        assert "database" not in response.text


def test_group_participant_grant_and_ineligible_decision(tasks_api):
    client, seed, identity_data, _, backend = tasks_api
    backend.identity.groups = Groups()
    repo, scope, _, _, token = identity_data
    repo.revoke_grant(scope, "participant", expected_revision=1)
    repo.add_membership("acme", TENANT, "group", GROUP)
    repo.save_grant(
        Grant("group-participant", scope, TENANT, "group", GROUP, "task_participant"),
        expected_revision=0,
    )
    client.headers["authorization"] = "Bearer " + token(groups=[GROUP])
    assigned = seed(users=(), groups=(GROUP,))
    hidden = seed(run="hidden", users=(OTHER,))
    assert client.get(BASE + "/tasks/" + assigned.task_id).status_code == 200
    response = client.post(
        BASE + "/tasks/" + hidden.task_id + "/decisions",
        json={
            "command_id": "ineligible",
            "expected_revision": 1,
            "decision": "approve",
        },
    )
    assert response.status_code == 403
    repo.revoke_grant(scope, "group-participant", expected_revision=1)
    assert client.get(BASE + "/tasks").status_code == 403


def test_provider_error_is_sanitized(tasks_api, tmp_path):
    _, _, identity_data, _, backend = tasks_api
    path = tmp_path / "missing-parent" / "private-tasks.db"
    with TestClient(
        create_app(replace(backend, tasks_database=path)), raise_server_exceptions=False
    ) as client:
        client.headers["authorization"] = "Bearer " + identity_data[-1]()
        response = client.get(BASE + "/tasks")
    assert response.status_code == 503
    assert "private-tasks" not in response.text and "sqlite" not in response.text
