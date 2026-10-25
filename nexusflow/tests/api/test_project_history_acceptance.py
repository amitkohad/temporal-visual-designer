# ruff: noqa: F811
"""Independent signed HTTP acceptance over the actual SQLite project history."""

from dataclasses import replace

import pytest

from nexusflow.tests.api.test_api import BASE, api, create  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401
from nexusflow.tests.package_promotion.test_activation import content

PROJECT = BASE + "/projects/hr"


def save(client, revision, value):
    response = client.put(
        PROJECT, json={"expected_revision": revision, "summary": "Edited", "content": value}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_history_cursor_survives_new_save_and_restore_preserves_snapshots(api):
    client, _, _, _ = api
    original = create(client).json()
    second = save(client, 1, content("B"))
    save(client, 2, content("C"))
    page = client.get(PROJECT + "/history?limit=2")
    assert page.status_code == 200, page.text
    assert [r["revision"] for r in page.json()["revisions"]] == [3, 2]
    assert page.json()["next_before_revision"] == 2
    assert all("content" not in item for item in page.json()["revisions"])
    save(client, 3, content("D"))
    tail = client.get(PROJECT + "/history?limit=2&before_revision=2").json()
    assert [r["revision"] for r in tail["revisions"]] == [1]
    assert tail["next_before_revision"] is None
    body = {"source_revision": 1, "expected_revision": 4, "summary": "Restore original"}
    restored = client.post(PROJECT + "/restore", json=body)
    assert restored.status_code == 201, restored.text
    assert restored.json()["revision"] == 5
    assert restored.json()["parent_revision"] == 4
    assert restored.json()["author_id"] == original["author_id"]
    assert restored.json()["content"] == original["content"]
    assert client.get(PROJECT + "?revision=1").json() == original
    assert client.get(PROJECT + "?revision=2").json() == second
    assert client.post(PROJECT + "/restore", json=body).status_code == 409
    assert client.get(PROJECT).json()["revision"] == 5


def test_compare_distinguishes_absent_null_and_layout_opt_in(api):
    client, _, _, _ = api
    first = content()
    first["layout"] = {"a/b~c": "original"}
    assert (
        client.post(
            BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": first}
        ).status_code
        == 201
    )
    second = content()
    second["layout"] = {"a/b~c": None}
    second["workflows"][0]["input_schema"]["added"] = None
    save(client, 1, second)
    endpoint = PROJECT + "/compare?before_revision=1&after_revision=2"
    plain = client.get(endpoint)
    assert plain.status_code == 200, plain.text
    assert all(not c["path"].startswith("/layout") for c in plain.json()["changes"])
    # Arrays remain atomic comparison values, preserving explicit JSON null.
    assert '"added":null' in plain.json()["changes"][0]["after_json"]
    layout = client.get(endpoint + "&include_layout=true").json()
    escaped = next(c for c in layout["changes"] if c["path"] == "/layout/a~1b~0c")
    assert escaped == {"path": "/layout/a~1b~0c", "before_json": '"original"', "after_json": "null"}
    third = content()
    third["layout"] = {}
    save(client, 2, third)
    removed = client.get(
        PROJECT + "/compare?before_revision=2&after_revision=3&include_layout=true"
    ).json()
    change = next(c for c in removed["changes"] if c["path"].startswith("/layout/"))
    assert change["before_json"] == "null" and change["after_json"] is None


def test_history_restore_permissions_are_fresh_and_environment_scoped(api):
    client, _, data, _ = api
    repository, scope, grant, _, _ = data
    assert create(client).status_code == 201
    for environment in ("uat", "prod"):
        repository.save_grant(
            replace(grant, scope=scope.model_copy(update={"environment_id": environment})),
            expected_revision=0,
        )
        target = PROJECT.replace("/dev/", f"/{environment}/")
        assert client.get(target + "/history").status_code == (404 if environment == "uat" else 403)
        assert (
            client.post(
                target + "/restore",
                json={"source_revision": 1, "expected_revision": 1, "summary": "Restore"},
            ).status_code
            == 403
        )
    assert client.get(PROJECT.replace("/acme/", "/other/") + "/history").status_code == 401
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(PROJECT + "/history").status_code == 403
    assert client.get(PROJECT + "/compare?before_revision=1&after_revision=1").status_code == 403
    assert (
        client.post(
            PROJECT + "/restore",
            json={"source_revision": 1, "expected_revision": 1, "summary": "Restore"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "before_revision=0", "before_revision=9223372036854775808"]
)
def test_history_rejects_invalid_pagination(api, query):
    client, _, _, _ = api
    assert create(client).status_code == 201
    assert client.get(PROJECT + "/history?" + query).status_code == 422


def test_history_compare_restore_need_no_catalog_or_runtime_provider(api, monkeypatch):
    client, backend, _, runtime = api
    assert create(client).status_code == 201
    save(client, 1, content("B"))

    def fail(*args, **kwargs):
        pytest.fail("History operation contacted optional provider")

    monkeypatch.setattr(backend.catalog, "get", fail, raising=False)
    monkeypatch.setattr(backend.capabilities, "supports", fail)
    monkeypatch.setattr(runtime, "dispatcher", fail)
    assert client.get(PROJECT + "/history").status_code == 200
    assert client.get(PROJECT + "/compare?before_revision=1&after_revision=2").status_code == 200
    result = client.post(
        PROJECT + "/restore",
        json={"source_revision": 1, "expected_revision": 2, "summary": "Restore"},
    )
    assert result.status_code == 201, result.text


@pytest.mark.parametrize(
    "layout", [{str(i): "x" for i in range(1001)}, {"secret": "sensitive" * 40000}]
)
def test_comparison_limits_are_safe_and_do_not_return_partial_private_data(api, layout):
    client, _, _, _ = api
    first = content()
    first["layout"] = {}
    assert (
        client.post(
            BASE + "/projects", json={"project_id": "hr", "name": "HR", "content": first}
        ).status_code
        == 201
    )
    second = content()
    second["layout"] = layout
    save(client, 1, second)
    response = client.get(
        PROJECT + "/compare?before_revision=1&after_revision=2&include_layout=true"
    )
    assert response.status_code == 422, response.text
    assert "sensitive" not in response.text
    assert "changes" not in response.json()


def test_missing_revision_and_forged_restore_leave_history_unchanged(api):
    client, _, _, _ = api
    original = create(client).json()
    assert client.get(PROJECT + "/compare?before_revision=1&after_revision=99").status_code == 404
    assert client.get(PROJECT + "/compare?before_revision=1").status_code == 422
    assert client.get(PROJECT + "/compare?before_revision=0&after_revision=1").status_code == 422
    body = {"source_revision": 1, "expected_revision": 1, "summary": "Restore"}
    assert (
        client.post(PROJECT + "/restore", json={**body, "author_id": "forged"}).status_code == 422
    )
    assert (
        client.post(PROJECT + "/restore", json={**body, "source_revision": 99}).status_code == 404
    )
    assert (
        client.post(PROJECT + "/restore", json={**body, "expected_revision": 2}).status_code == 409
    )
    assert client.get(PROJECT).json() == original
    assert len(client.get(PROJECT + "/history").json()["revisions"]) == 1
