# ruff: noqa: F811
"""Independent signed SQLite acceptance for built-in project starters."""

from dataclasses import replace

import pytest

from nexusflow.tests.api.test_api import BASE, api  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401


def request_body(template, project="starter"):
    slots = ("manager", "hr", "it") if template == "employee-onboarding" else ("manager", "finance")
    return {
        "project_id": project,
        "name": "Business starter",
        "assignments": {slot: {"user_ids": ["business-user"], "group_ids": []} for slot in slots},
    }


@pytest.mark.parametrize("template", ["employee-onboarding", "purchase-requisition"])
def test_template_creates_independent_revision_without_release_or_execution(api, template):
    client, _, _, runtime = api
    listed = client.get(BASE + "/project-templates")
    assert listed.status_code == 200, listed.text
    assert {item["template_id"] for item in listed.json()} == {
        "employee-onboarding",
        "purchase-requisition",
    }
    endpoint = BASE + "/project-templates/" + template
    info = client.get(endpoint)
    assert info.status_code == 200, info.text
    assert info.json()["version"] == "1"
    body = request_body(template)
    created = client.post(endpoint + "/projects", json=body)
    assert created.status_code == 201, created.text
    original = created.json()
    assert original["revision"] == 1
    assert original["content"]["workflows"][0]["workflow_id"] == info.json()["workflow_id"]
    assert client.post(endpoint + "/projects", json=body).status_code == 409
    other = client.post(endpoint + "/projects", json=request_body(template, "other"))
    assert other.status_code == 201, other.text
    changed = other.json()["content"]
    changed["layout"] = {"private_note": "Private draft edit"}
    response = client.put(
        BASE + "/projects/other",
        json={
            "expected_revision": 1,
            "summary": "Changed",
            "content": changed,
        },
    )
    assert response.status_code == 200, response.text
    assert client.get(BASE + "/projects/starter").json() == original
    assert client.get(endpoint).json() == info.json()
    assert runtime.plans == {}
    activation = client.get(BASE + "/projects/starter/activation")
    assert activation.status_code == 200, activation.text
    assert activation.json()["release_id"] is None
    assert activation.json()["revision"] == 0


def test_template_assignments_are_strict_and_authorization_is_fresh(api):
    client, _, data, _ = api
    repository, scope, grant, _, _ = data
    endpoint = BASE + "/project-templates/employee-onboarding/projects"
    body = request_body("employee-onboarding")
    for forged in ({"author_id": "forged"}, {"content": {}}, {"environment_id": "prod"}):
        assert client.post(endpoint, json={**body, **forged}).status_code == 422
    for assignments in ({}, {"unknown": {"user_ids": ["x"]}}, {"manager": {"user_ids": []}}):
        assert client.post(endpoint, json={**body, "assignments": assignments}).status_code == 422
    assert client.get(BASE + "/projects").json() == []
    for environment in ("uat", "prod"):
        repository.save_grant(
            replace(grant, scope=scope.model_copy(update={"environment_id": environment})),
            expected_revision=0,
        )
        assert (
            client.post(endpoint.replace("/dev/", f"/{environment}/"), json=body).status_code == 403
        )
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(BASE + "/project-templates").status_code == 403
    assert client.post(endpoint, json=body).status_code == 403
