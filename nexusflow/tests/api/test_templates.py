"""Signed HTTP template discovery and isolated draft creation acceptance."""

from dataclasses import replace

import pytest

from nexusflow.apps.api import create_app
from nexusflow.tests.api.test_api import BASE
from nexusflow.tests.api.test_api import api as api
from nexusflow.tests.identity.test_service import identity as identity


def payload(project_id="people", slots=("manager", "hr", "it")):
    return {
        "project_id": project_id,
        "name": "People onboarding",
        "assignments": {slot: {"user_ids": ["manager-user"]} for slot in slots},
    }


def test_discover_create_and_edit_independent_drafts(api):
    client, backend, identity_data, runtime = api
    listed = client.get(BASE + "/project-templates")
    assert listed.status_code == 200, listed.text
    assert {item["template_id"] for item in listed.json()} == {
        "employee-onboarding",
        "purchase-requisition",
    }
    for item in listed.json():
        assert client.get(BASE + "/project-templates/" + item["template_id"]).json() == item
        assert item["version"] == "1"
        assert item["input_schema"]
    url = BASE + "/project-templates/employee-onboarding/projects"
    first = client.post(url, json=payload())
    assert first.status_code == 201, first.text
    original = first.json()
    assert original["revision"] == 1 and original["parent_revision"] is None
    assert original["author_id"] == "pairwise-subject"
    second = client.post(url, json=payload("people-two"))
    assert second.status_code == 201, second.text
    assert second.json()["content"] == original["content"]
    assert client.post(url, json=payload()).status_code == 409
    changed = original["content"]
    changed["layout"] = {"canvas": {"x": 42}}
    saved = client.put(
        BASE + "/projects/people",
        json={"expected_revision": 1, "summary": "Canvas edit", "content": changed},
    )
    assert saved.status_code == 200, saved.text
    assert client.get(BASE + "/projects/people-two").json() == second.json()
    scope = identity_data[1]
    assert backend.activations.current(scope, "people") is None
    assert backend.projects.published(scope, "people", "template") is None
    assert runtime.plans == {} and runtime.placements == []
    # The ordinary API fixture has no human task provider: draft creation needs none.
    assert backend.tasks_database is None


def test_purchase_requisition_project(api):
    client, _, _, _ = api
    result = client.post(
        BASE + "/project-templates/purchase-requisition/projects",
        json=payload("purchasing", ("manager", "finance")),
    )
    assert result.status_code == 201, result.text
    assert result.json()["content"]["workflows"][0]["workflow_id"] == "purchase-requisition"


def test_group_assignment_and_all_approvers(api):
    client, _, _, _ = api
    body = payload()
    body["assignments"]["manager"] = {"rule": "all_approvers", "group_ids": ["entra-managers"]}
    response = client.post(BASE + "/project-templates/employee-onboarding/projects", json=body)
    assert response.status_code == 201, response.text
    approvals = response.json()["content"]["workflows"][0]["nodes"]
    manager = next(node for node in approvals if node["node_id"] == "manager")
    assignment = manager["input_mapping"]["human_task"]
    assert assignment["rule"] == "all_approvers"
    assert assignment["group_ids"] == ["entra-managers"] and assignment["user_ids"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "injected"},
        {"scope": {"environment_id": "prod"}},
        {"content": {}},
        {"name": True},
        {"project_id": True},
        {"assignments": {}},
        {"assignments": {slot: {} for slot in ("manager", "hr", "it")}},
        {"assignments": {"manager": {"user_ids": ["user"]}}},
        {"assignments": {slot: {"user_ids": [True]} for slot in ("manager", "hr", "it")}},
        {
            "assignments": {
                slot: {"user_ids": ["user"], "rule": "any"} for slot in ("manager", "hr", "it")
            }
        },
        {
            "assignments": {
                slot: {"user_ids": ["user"], "actor_id": "injected"}
                for slot in ("manager", "hr", "it")
            }
        },
    ],
)
def test_strict_template_creation(api, change):
    client, backend, identity_data, _ = api
    result = client.post(
        BASE + "/project-templates/employee-onboarding/projects", json={**payload(), **change}
    )
    assert result.status_code == 422, result.text
    assert backend.projects.list_projects(identity_data[1]) == ()


def test_extra_assignment_and_unknown_template(api):
    client, _, _, _ = api
    body = payload()
    body["assignments"]["other"] = {"user_ids": ["user"]}
    assert (
        client.post(BASE + "/project-templates/employee-onboarding/projects", json=body).status_code
        == 422
    )
    assert client.get(BASE + "/project-templates/missing").status_code == 404
    assert (
        client.post(BASE + "/project-templates/missing/projects", json=payload()).status_code == 404
    )


@pytest.mark.parametrize("suffix", ["", "/employee-onboarding", "/employee-onboarding/projects"])
def test_template_routes_recheck_permission(api, suffix):
    client, _, identity_data, _ = api

    def request():
        url = BASE + "/project-templates" + suffix
        return client.post(url, json=payload()) if suffix.endswith("projects") else client.get(url)

    header = client.headers.pop("authorization")
    assert request().status_code == 401
    client.headers["authorization"] = header
    repo, scope, grant, _, _ = identity_data
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert request().status_code == 403


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_template_creation_is_dev_only(api, environment):
    client, backend, identity_data, _ = api
    repo, scope, grant, _, _ = identity_data
    other_scope = scope.model_copy(update={"environment_id": environment})
    repo.save_grant(replace(grant, scope=other_scope), expected_revision=0)
    base = BASE.replace("/dev", "/" + environment)
    assert client.get(base + "/project-templates").status_code == (
        200 if environment == "uat" else 403
    )
    assert (
        client.post(
            base + "/project-templates/employee-onboarding/projects", json=payload()
        ).status_code
        == 403
    )
    assert backend.projects.list_projects(other_scope) == ()
    assert client.get(BASE.replace("acme", "other") + "/project-templates").status_code == 401


def test_template_openapi(api):
    _, backend, _, _ = api
    schema = create_app(backend).openapi()
    prefix = (
        "/api/v1/organizations/{organization_id}/environments/{environment_id}/project-templates"
    )
    assert schema["paths"][prefix]["get"]["responses"]["200"]
    assert schema["paths"][prefix + "/{template_id}/projects"]["post"]["responses"]["201"]
    models = schema["components"]["schemas"]
    assert models["CreateTemplateProject"]["additionalProperties"] is False
    assert models["TemplateAssignmentRequest"]["additionalProperties"] is False
    assert "input_schema" in models["TemplateInfo"]["properties"]
