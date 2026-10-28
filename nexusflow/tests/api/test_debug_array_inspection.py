"""Signed HTTP inspection of explicitly opted-in business input collections."""

# ruff: noqa: F811
import copy
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from nexusflow.apps.api import create_app
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401
from nexusflow.tests.api.test_debug_session_acceptance import OTHER, MetadataRuntime, request
from nexusflow.tests.package_promotion.test_activation import content


def public(kind):
    return {"type": kind, "x-nexusflow-inspect": True}


def collection(items, **extra):
    return {
        "type": "array",
        "x-nexusflow-inspect": True,
        "x-nexusflow-inspect-array": True,
        "items": items,
        **extra,
    }


@pytest.fixture
def arrays(api, tmp_path):
    original, backend, data, _ = api
    sessions = SQLiteDebugSessionRepository(tmp_path / "array-http.db")
    runtime = MetadataRuntime()
    configured = replace(backend, debug_sessions=sessions, debug_runtime=runtime)
    counter = 0
    with TestClient(create_app(configured), raise_server_exceptions=False) as client:
        client.headers.update(original.headers)

        def start(properties, inputs):
            nonlocal counter
            counter += 1
            project_id = f"arrays-{counter}"
            project = content()
            project["workflows"][0]["input_schema"] = {
                "type": "object",
                "properties": copy.deepcopy(properties),
            }
            created = client.post(
                BASE + "/projects",
                json={"project_id": project_id, "name": "Collections", "content": project},
            )
            assert created.status_code == 201, created.text
            path = BASE + f"/projects/{project_id}/debug-sessions"
            accepted = client.post(path, json=request(input=inputs))
            assert accepted.status_code == 202, accepted.text
            inspection = path + "/" + accepted.json()["session_id"] + "/inputs"
            return inspection, accepted.json(), project_id, project

        yield client, configured, data, runtime, start
    sessions.close()


def test_purchase_items_disclose_public_leaves_without_private_dynamic_names(arrays, monkeypatch):
    client, backend, _, runtime, start = arrays
    schema = collection(
        {
            "type": "object",
            "properties": {
                "description": public("string"),
                "quantity": public("integer"),
                "vendor_token": {**public("string"), "x-secret": True},
                "notes": {"type": "string"},
            },
        }
    )
    path, session, _, _ = start(
        {"items": schema},
        {
            "items": [
                {
                    "description": "Laptop",
                    "quantity": 2,
                    "vendor_token": "private-vendor-value",
                    "notes": "unclassified-purchase-note",
                    "private-dynamic-field-name": "private-dynamic-value",
                },
                {"description": "Mouse", "quantity": 3, "vendor_token": "second-private-token"},
            ]
        },
    )
    monkeypatch.setattr(runtime, "snapshot", Mock(side_effect=OSError("Worker unavailable")))
    monkeypatch.setattr(runtime, "control", Mock(side_effect=OSError("Worker unavailable")))
    changes = backend.debug_sessions.connection.total_changes
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"] == {
        "items": [{"description": "Laptop", "quantity": 2}, {"description": "Mouse", "quantity": 3}]
    }
    assert body["definition_digest"] == session["definition_digest"]
    assert "/items/0/vendor_token" in body["redacted_paths"]
    assert "/items/0" in body["redacted_paths"]
    assert not body["truncated"]
    for sentinel in (
        "private-vendor-value",
        "unclassified-purchase-note",
        "private-dynamic-field-name",
        "private-dynamic-value",
        "second-private-token",
    ):
        assert sentinel not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert backend.debug_sessions.connection.total_changes == changes
    runtime.snapshot.assert_not_called()
    runtime.control.assert_not_called()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "x-nexusflow-inspect": True, "items": public("string")},
        collection(public("string"), **{"x-nexusflow-inspect-array": 1}),
        collection({"type": "string"}),
        collection({**public("string"), "writeOnly": True}),
        collection({**public("string"), "format": "password"}),
        collection(public("string"), **{"x-sensitive": True}),
        collection(public("string"), contains={"const": "private-array-sentinel"}),
        collection(public("string"), prefixItems=[public("string")]),
    ],
)
def test_arrays_without_new_opt_in_or_with_protected_unsupported_policy_stay_hidden(arrays, schema):
    client, _, _, _, start = arrays
    path, _, _, _ = start({"collection": schema}, {"collection": ["private-array-sentinel"]})
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["values"] == {}
    assert response.json()["redacted_paths"] == ["/collection"]
    assert "private-array-sentinel" not in response.text


def test_scalar_limits_keep_array_positions_and_tail_cap_is_reported(arrays):
    client, _, _, _, start = arrays
    path, _, _, _ = start(
        {"names": collection(public("string")), "amounts": collection(public("integer"))},
        {"names": ["first", "x" * 1100, "last"], "amounts": list(range(103))},
    )
    response = client.get(path)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"]["names"] == ["first", None, "last"]
    assert body["values"]["amounts"] == list(range(100))
    assert {"/names/1", "/amounts"} <= set(body["redacted_paths"])
    assert body["truncated"] is True
    assert len(response.content) <= 16384


def test_nested_arrays_need_opt_in_at_each_layer(arrays):
    client, _, _, _, start = arrays
    path, _, _, _ = start(
        {
            "public_grid": collection(collection(public("integer"))),
            "hidden_grid": collection(
                {"type": "array", "x-nexusflow-inspect": True, "items": public("integer")}
            ),
        },
        {"public_grid": [[1, 2], [3]], "hidden_grid": [[999]]},
    )
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["values"] == {"public_grid": [[1, 2], [3]]}
    assert "999" not in response.text


def test_aggregate_array_projection_still_obeys_whole_response_budget(arrays):
    client, _, _, _, start = arrays
    path, _, _, _ = start(
        {"collection": collection(public("string"))}, {"collection": ["p" * 900] * 60}
    )
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert len(response.content) <= 16384
    assert response.json()["values"] == {}
    assert response.json()["redacted_paths"] == [""]
    assert response.json()["truncated"] is True


def test_new_array_annotation_on_current_draft_does_not_declassify_old_session(arrays):
    client, _, _, _, start = arrays
    old = {"type": "array", "x-nexusflow-inspect": True, "items": public("string")}
    inputs = {"collection": ["new-session-public-only"]}
    old_path, _, project_id, project = start({"collection": old}, inputs)
    project["workflows"][0]["input_schema"]["properties"]["collection"][
        "x-nexusflow-inspect-array"
    ] = True
    saved = client.put(
        BASE + f"/projects/{project_id}",
        json={
            "expected_revision": 1,
            "summary": "Opt into collection inspection",
            "content": project,
        },
    )
    assert saved.status_code == 200, saved.text
    old_response = client.get(old_path)
    assert old_response.status_code == 200, old_response.text
    assert old_response.json()["values"] == {}
    assert "new-session-public-only" not in old_response.text
    path = BASE + f"/projects/{project_id}/debug-sessions"
    new_session = client.post(
        path, json=request(operation_id="new-annotation", source_revision=2, input=inputs)
    )
    assert new_session.status_code == 202, new_session.text
    inspected = client.get(path + "/" + new_session.json()["session_id"] + "/inputs")
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["values"] == inputs
    assert inspected.json()["source_revision"] == 2


def test_collection_inspection_keeps_fresh_owner_authorization_before_source_lookup(
    arrays, monkeypatch
):
    client, backend, data, _, start = arrays
    path, _, _, _ = start({"items": collection(public("string"))}, {"items": ["owner-value"]})
    repository, scope, grant, _, token = data
    repository.add_membership("acme", grant.directory_tenant_id, "user", OTHER)
    repository.save_grant(
        replace(grant, grant_id="other-array-owner", principal_id=OTHER), expected_revision=0
    )
    source = Mock(side_effect=AssertionError("Unauthorized input source access"))
    monkeypatch.setattr(backend.projects, "revision", source)
    client.headers["authorization"] = "Bearer " + token(oid=OTHER, sub="other-array-subject")
    assert client.get(path).status_code == 403
    client.headers["authorization"] = "Bearer " + token()
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(path).status_code == 403
    source.assert_not_called()
