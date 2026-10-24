"""Signed HTTP task-form permissions, validation and durable approval boundaries."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.human_tasks.form_types import TaskFieldAccess
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.api.test_tasks import (  # noqa: F401
    OTHER,
    TrustedWorker,
    tasks_api,
)
from nexusflow.tests.designer_forms.test_forms import definition
from nexusflow.tests.identity.test_service import TENANT, USER, identity  # noqa: F401


class Fields:
    def __init__(self):
        self.calls = []
        self.denied = False

    def access(self, scope, task_id, actor_id):
        self.calls.append((scope, task_id, actor_id))
        if self.denied:
            raise OSError("PRIVATE-POLICY-FAILURE")
        return TaskFieldAccess(
            frozenset({"name", "amount", "approved", "token"}),
            frozenset({"name", "amount", "token"}),
        )


@pytest.fixture
def forms_api(tasks_api):  # noqa: F811
    _, seed, identity_data, tasks, backend = tasks_api
    fields = Fields()
    forms = TaskFormService(backend.tasks_database, TrustedWorker())

    def bind(**kwargs):
        task = seed(**kwargs)
        revision = forms.bind(
            task.scope,
            task.task_id,
            json.dumps(definition()),
            json.dumps({"private": "PRIVATE-VALUE", "token": "WRITEONLY-VALUE"}),
        )
        return task, revision

    with TestClient(
        create_app(replace(backend, task_fields=fields)), raise_server_exceptions=False
    ) as client:
        client.headers["authorization"] = "Bearer " + identity_data[-1]()
        yield client, bind, identity_data, tasks, fields, backend
    forms.close()


def test_preview_redacts_fields_values_and_uses_verified_oid(forms_api):
    client, bind, identity_data, _, fields, _ = forms_api
    task, revision = bind()
    response = client.get(BASE + "/tasks/" + task.task_id + "/form")
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == revision
    assert [field["field_id"] for field in response.json()["fields"]] == [
        "amount",
        "name",
        "approved",
        "token",
    ]
    assert json.loads(response.json()["values_json"]) == {}
    assert "PRIVATE-VALUE" not in response.text and "WRITEONLY-VALUE" not in response.text
    assert fields.calls == [(identity_data[1], task.task_id, USER)]


def test_valid_submission_is_audited_and_unlocks_approval(forms_api):
    client, bind, _, _, _, _ = forms_api
    task, revision = bind()
    url = BASE + "/tasks/" + task.task_id
    decision = {"command_id": "approve", "expected_revision": revision, "decision": "approve"}
    assert client.post(url + "/decisions", json=decision).status_code == 409
    result = client.post(
        url + "/form", json={"expected_revision": revision, "values": {"name": "Ada", "amount": 12}}
    )
    assert result.status_code == 200, result.text
    updated = result.json()["revision"]
    assert updated == revision + 1
    assert client.get(url).json()["revision"] == updated
    stale = client.post(
        url + "/form", json={"expected_revision": revision, "values": {"amount": 20}}
    )
    assert stale.status_code == 409
    decision["expected_revision"] = updated
    approved = client.post(url + "/decisions", json=decision)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    events = client.get(url + "/audit").json()
    assert [event["kind"] for event in events] == [
        "created",
        "form_bound",
        "form_updated",
        "decided",
    ]
    assert "PRIVATE-VALUE" not in json.dumps(events) and "Ada" not in json.dumps(events)
    preview = client.get(url + "/form")
    assert preview.status_code == 200
    assert all(not field["editable"] for field in preview.json()["fields"])
    assert (
        client.post(
            url + "/form", json={"expected_revision": updated + 1, "values": {}}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "values,status",
    [
        ({"amount": -1, "name": "SECRET-INVALID"}, 422),
        ({"private": "SECRET-INVALID"}, 403),
        ({"approved": True}, 403),
        ({"unknown": "SECRET-INVALID"}, 403),
    ],
)
def test_invalid_submission_is_safe_and_does_not_change_revision(forms_api, values, status):
    client, bind, _, _, _, _ = forms_api
    task, revision = bind()
    url = BASE + "/tasks/" + task.task_id
    response = client.post(url + "/form", json={"expected_revision": revision, "values": values})
    assert response.status_code == status, response.text
    assert "SECRET-INVALID" not in response.text
    assert client.get(url).json()["revision"] == revision


def test_form_scope_eligibility_and_revocation(forms_api):
    client, bind, identity_data, _, _, _ = forms_api
    task, revision = bind()
    hidden, _ = bind(run="hidden", users=(OTHER,))
    assert client.get(BASE + "/tasks/" + hidden.task_id + "/form").status_code == 403
    assert (
        client.post(
            BASE + "/tasks/" + hidden.task_id + "/form",
            json={"expected_revision": revision, "values": {}},
        ).status_code
        == 403
    )
    repo, scope, _, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(
        Grant("participant", uat, TENANT, "user", USER, "task_participant"), expected_revision=0
    )
    assert (
        client.get(BASE.replace("/dev", "/uat") + "/tasks/" + task.task_id + "/form").status_code
        == 404
    )
    repo.revoke_grant(scope, "participant", expected_revision=1)
    assert client.get(BASE + "/tasks/" + task.task_id + "/form").status_code == 403
    assert (
        client.post(
            BASE + "/tasks/" + task.task_id + "/form",
            json={"expected_revision": revision, "values": {}},
        ).status_code
        == 403
    )


def test_form_provider_and_field_policy_are_required(forms_api, api):  # noqa: F811
    client, bind, identity_data, _, fields, backend = forms_api
    task, revision = bind()
    url = BASE + "/tasks/" + task.task_id + "/form"
    fields.denied = True
    response = client.get(url)
    assert response.status_code == 503 and "PRIVATE-POLICY" not in response.text
    with TestClient(create_app(backend), raise_server_exceptions=False) as unconfigured:
        unconfigured.headers["authorization"] = "Bearer " + identity_data[-1]()
        assert unconfigured.get(url).status_code == 503
        assert (
            unconfigured.post(url, json={"expected_revision": revision, "values": {}}).status_code
            == 503
        )
    disabled = api[0]
    assert disabled.get(url).status_code == 503


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": 0, "values": {}},
        {"expected_revision": "2", "values": {}},
        {
            "expected_revision": 2,
            "values": [],
        },
        {"expected_revision": 2, "values": {}, "readable_fields": ["private"]},
    ],
)
def test_form_request_is_strict_and_has_no_internal_routes(forms_api, payload):
    client, bind, _, _, _, _ = forms_api
    task, _ = bind()
    url = BASE + "/tasks/" + task.task_id
    assert client.post(url + "/form", json=payload).status_code == 422
    assert client.post(url + "/form/bind", json={}).status_code == 404
    assert client.get(url + "/form/attachments").status_code == 404
