"""Signed stage handoff and shared-form behavior through the actual task API."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.human_tasks.form_types import TaskFieldAccess
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.human_tasks.types import ApprovalStage, CreateTask
from nexusflow.modules.identity_environments.types import Grant
from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.api.test_tasks import OTHER, tasks_api  # noqa: F401
from nexusflow.tests.identity.test_service import TENANT, USER, identity  # noqa: F401


def staged_request(second_user=OTHER):
    return CreateTask(
        run_id="staged-run",
        node_invocation_id="purchase-approval",
        definition_revision="r1",
        rule="sequential",
        stages=(
            ApprovalStage(stage_id="manager", rule="any_one", user_ids=(USER,)),
            ApprovalStage(stage_id="finance", rule="all_approvers", user_ids=(second_user,)),
        ),
    )


def test_signed_stage_handoff_and_own_receipt_retry(tasks_api):  # noqa: F811
    client, _, identity_data, core, _ = tasks_api
    repo, scope, _, _, token = identity_data
    task = core.create(scope, staged_request())
    repo.add_membership(scope.organization_id, TENANT, "user", OTHER)
    repo.save_grant(
        Grant("finance-participant", scope, TENANT, "user", OTHER, "task_participant"),
        expected_revision=0,
    )
    url = BASE + "/tasks/" + task.task_id
    payload = {"command_id": "manager-vote", "expected_revision": 1, "decision": "approve"}
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="finance-subject")
    assert client.get(url).status_code == 403
    assert client.post(url + "/decisions", json=payload).status_code == 403
    client.headers["authorization"] = "Bearer " + token()
    advanced = client.post(url + "/decisions", json=payload)
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["stage_index"] == 1
    assert advanced.json()["status"] == "pending"
    assert advanced.json()["eligible_approver_ids"] == [OTHER]
    assert core.pending_completions(scope) == ()
    assert client.get(url).status_code == 403
    assert client.post(url + "/decisions", json=payload).json() == advanced.json()
    assert (
        client.post(
            url + "/decisions",
            json={**payload, "command_id": "new-old-stage", "expected_revision": 2},
        ).status_code
        == 403
    )
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="finance-subject")
    final = client.post(
        url + "/decisions",
        json={"command_id": "finance-vote", "expected_revision": 2, "decision": "approve"},
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "approved"
    assert [vote["stage_id"] for vote in final.json()["decisions"]] == ["manager", "finance"]
    assert len(core.pending_completions(scope)) == 1
    events = client.get(url + "/audit").json()
    transition = next(event for event in events if event["kind"] == "stage_advanced")
    assert (transition["previous_stage_id"], transition["stage_id"]) == ("manager", "finance")


class Fields:
    def access(self, scope, task_id, actor_id):
        return TaskFieldAccess(frozenset({"reason"}), frozenset({"reason"}))


def test_shared_form_is_validated_once_and_frozen_across_stages(tasks_api):  # noqa: F811
    _, _, identity_data, core, backend = tasks_api
    _, scope, _, _, token = identity_data
    task = core.create(scope, staged_request(USER))
    forms = TaskFormService(backend.tasks_database, core.authorization, Fields())
    try:
        revision = forms.bind(
            scope,
            task.task_id,
            json.dumps(
                {
                    "form_id": "purchase",
                    "schema_version": "0.1",
                    "data_schema": {
                        "type": "object",
                        "properties": {"reason": {"type": "string"}},
                        "required": ["reason"],
                        "additionalProperties": False,
                    },
                }
            ),
        )
    finally:
        forms.close()
    url = BASE + "/tasks/" + task.task_id
    with TestClient(create_app(replace(backend, task_fields=Fields()))) as client:
        client.headers["authorization"] = "Bearer " + token()
        vote = {"command_id": "manager", "expected_revision": revision, "decision": "approve"}
        assert client.post(url + "/decisions", json=vote).status_code == 409
        submission = client.post(
            url + "/form", json={"expected_revision": revision, "values": {"reason": "purchase"}}
        )
        assert submission.status_code == 200, submission.text
        vote["expected_revision"] = submission.json()["revision"]
        advanced = client.post(url + "/decisions", json=vote).json()
        assert advanced["stage_index"] == 1 and advanced["status"] == "pending"
        preview = client.get(url + "/form").json()
        assert all(not field["editable"] for field in preview["fields"])
        assert (
            client.post(
                url + "/form",
                json={"expected_revision": advanced["revision"], "values": {"reason": "changed"}},
            ).status_code
            == 409
        )
        final = client.post(
            url + "/decisions",
            json={
                "command_id": "finance",
                "expected_revision": advanced["revision"],
                "decision": "approve",
            },
        )
        assert final.status_code == 200 and final.json()["status"] == "approved"
        assert len(core.pending_completions(scope)) == 1
