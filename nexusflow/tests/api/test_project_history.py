"""Signed HTTP history, bounded comparison and CAS restoration acceptance."""

from dataclasses import replace

import pytest

from nexusflow.apps.api import create_app
from nexusflow.tests.api.test_api import BASE, create, publish_activate
from nexusflow.tests.api.test_api import api as api
from nexusflow.tests.identity.test_service import identity as identity
from nexusflow.tests.package_promotion.test_activation import content


def save(client, revision=1, name="B"):
    response = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": revision, "summary": "Edit", "content": content(name)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_history_metadata_pagination_and_restore_leave_release_active(api):
    client, _, _, _ = api
    release = publish_activate(client)
    original = client.get(BASE + "/projects/hr").json()
    edited = save(client)
    page = client.get(BASE + "/projects/hr/history?limit=1").json()
    assert page["project_id"] == "hr"
    assert page["revisions"] == [
        {k: v for k, v in edited.items() if k not in {"project_id", "content"}}
    ]
    assert page["next_before_revision"] == 2
    older = client.get(BASE + "/projects/hr/history?limit=1&before_revision=2").json()
    assert older["revisions"][0]["revision"] == 1
    assert older["next_before_revision"] is None
    assert "content" not in page["revisions"][0]
    payload = {"source_revision": 1, "expected_revision": 2, "summary": "Restore initial"}
    restored = client.post(BASE + "/projects/hr/restore", json=payload)
    assert restored.status_code == 201, restored.text
    assert restored.json()["content"] == original["content"]
    assert restored.json()["revision"] == 3
    assert restored.json()["parent_revision"] == 2
    assert restored.json()["author_id"] == original["author_id"]
    assert client.post(BASE + "/projects/hr/restore", json=payload).status_code == 409
    assert client.get(BASE + "/projects/hr?revision=2").json() == edited
    assert client.get(BASE + "/projects/hr/activation").json()["release_id"] == release
    assert client.get(BASE + "/projects/hr/history").json()["revisions"][0]["revision"] == 3


def test_comparison_layout_opt_in_and_json_strings(api):
    client, _, _, _ = api
    assert create(client).status_code == 201
    changed = content()
    changed["layout"] = {"canvas": None}
    response = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "Layout", "content": changed},
    )
    assert response.status_code == 200, response.text
    url = BASE + "/projects/hr/compare?before_revision=1&after_revision=2"
    assert client.get(url).json()["changes"] == []
    compared = client.get(url + "&include_layout=true")
    assert compared.status_code == 200, compared.text
    result = compared.json()
    assert result["include_layout"] is True
    assert result["before_revision"] == 1 and result["after_revision"] == 2
    assert result["changes"]
    assert any(
        item["path"] == "/layout/canvas"
        and item["before_json"] is None
        and item["after_json"] == "null"
        for item in result["changes"]
    )
    assert (
        client.get(BASE + "/projects/hr/compare?before_revision=1&after_revision=1").json()[
            "changes"
        ]
        == []
    )


@pytest.mark.parametrize(
    "suffix",
    [
        "history?limit=0",
        "history?limit=101",
        "history?before_revision=0",
        "history?before_revision=9223372036854775808",
        "compare",
        "compare?before_revision=0&after_revision=1",
        "compare?before_revision=1&after_revision=9223372036854775808",
        "compare?before_revision=1&after_revision=2&include_layout=secret",
    ],
)
def test_invalid_history_and_compare_queries_are_safe(api, suffix):
    client, _, _, _ = api
    response = client.get(BASE + "/projects/hr/" + suffix)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_input"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "updates",
    [
        {"source_revision": True},
        {"source_revision": "1"},
        {"source_revision": 0},
        {"expected_revision": 9223372036854775808},
        {"summary": ""},
        {"summary": " "},
        {"summary": "x" * 1001},
        {"actor_id": "forged"},
        {"scope": {"environment_id": "prod"}},
        {"content": {}},
    ],
)
def test_restore_body_is_strict_and_rejected_without_revision(api, updates):
    client, _, _, _ = api
    assert create(client).status_code == 201
    response = client.post(
        BASE + "/projects/hr/restore",
        json={"source_revision": 1, "expected_revision": 1, "summary": "Restore", **updates},
    )
    assert response.status_code == 422, response.text
    assert "forged" not in response.text
    assert len(client.get(BASE + "/projects/hr/history").json()["revisions"]) == 1


@pytest.mark.parametrize(
    "suffix", ["history", "compare?before_revision=1&after_revision=2", "restore"]
)
def test_history_operations_recheck_authentication_and_revocation(api, suffix):
    client, _, identity_data, _ = api
    assert create(client).status_code == 201

    def request():
        if suffix == "restore":
            return client.post(
                BASE + "/projects/hr/restore",
                json={"source_revision": 1, "expected_revision": 1, "summary": "Restore"},
            )
        return client.get(BASE + "/projects/hr/" + suffix)

    header = client.headers.pop("authorization")
    assert request().status_code == 401
    client.headers["authorization"] = header
    repo, scope, grant, _, _ = identity_data
    repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert request().status_code == 403


def test_missing_resources_scope_isolation_and_dev_only_restore(api):
    client, _, identity_data, _ = api
    assert client.get(BASE + "/projects/missing/history").status_code == 404
    assert create(client).status_code == 201
    assert (
        client.get(BASE + "/projects/hr/compare?before_revision=1&after_revision=2").status_code
        == 404
    )
    assert (
        client.post(
            BASE + "/projects/hr/restore",
            json={"source_revision": 2, "expected_revision": 1, "summary": "Restore"},
        ).status_code
        == 404
    )
    repo, scope, grant, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(replace(grant, scope=uat), expected_revision=0)
    other = BASE.replace("/dev", "/uat")
    assert client.get(other + "/projects/hr/history").status_code == 404
    assert (
        client.post(
            other + "/projects/hr/restore",
            json={"source_revision": 1, "expected_revision": 1, "summary": "Restore"},
        ).status_code
        == 403
    )
    assert client.get(BASE + "/projects/hr?revision=9223372036854775808").status_code == 422


def test_history_openapi_contracts(api):
    _, backend, _, _ = api
    schema = create_app(backend).openapi()
    prefix = "/api/v1/organizations/{organization_id}/environments/{environment_id}/projects/{project_id}"
    assert schema["paths"][prefix + "/restore"]["post"]["responses"]["201"]
    models = schema["components"]["schemas"]
    assert models["RestoreProject"]["additionalProperties"] is False
    assert "content" not in models["ProjectRevisionMetadata"]["properties"]
    assert set(models["ProjectComparison"]["required"]) == {
        "project_id",
        "before_revision",
        "after_revision",
        "include_layout",
        "changes",
    }


def test_comparison_limit_returns_safe_422_without_partial_changes(api):
    client, _, _, _ = api
    assert create(client).status_code == 201
    changed = content()
    changed["layout"] = {f"private-key-{number}": "private-value" for number in range(1001)}
    response = client.put(
        BASE + "/projects/hr",
        json={"expected_revision": 1, "summary": "Large layout", "content": changed},
    )
    assert response.status_code == 200, response.text
    response = client.get(
        BASE + "/projects/hr/compare?before_revision=1&after_revision=2&include_layout=true"
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "invalid_input"
    assert "private" not in response.text
    assert "changes" not in response.json()
