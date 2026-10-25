"""Independent signed HTTP acceptance for deployment evidence and immutable history."""
# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor

import pytest

from nexusflow.tests.api.test_api import BASE, api, create  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401

PROJECT = BASE + "/projects/hr"


def release(client):
    assert create(client).status_code == 201
    response = client.post(
        PROJECT + "/releases", json={"source_revision": 1, "idempotency_key": "release-a"}
    )
    assert response.status_code == 201, response.text
    return response.json()["release_id"]


def request(release_id, operation="deploy-a", revision=0, **extra):
    return {
        "project_id": "hr",
        "release_id": release_id,
        "operation_id": operation,
        "configuration_revision": "cfg-1",
        "expected_revision": revision,
        **extra,
    }


def test_evidence_retry_conflict_and_metadata_only_stable_history(api):
    client, _, _, runtime = api
    release_id = release(client)
    payload = request(release_id, evidence={"change_request_number": "CR-123", "notes": "Approved"})
    deployed = client.post(PROJECT + "/activation", json=payload)
    assert deployed.status_code == 200, deployed.text
    original = deployed.json()
    assert original["evidence"] == payload["evidence"]
    assert original["actor_id"] == "pairwise-subject"
    assert client.post(PROJECT + "/activation", json=payload).json() == original
    changed = {**payload, "evidence": {"change_request_number": "CR-999"}}
    assert client.post(PROJECT + "/activation", json=changed).status_code == 409
    second = client.post(PROJECT + "/activation", json=request(release_id, "deploy-b", 1))
    assert second.status_code == 200, second.text
    page = client.get(PROJECT + "/activations?limit=1").json()
    assert [item["revision"] for item in page["items"]] == [2]
    assert page["next_before_revision"] == 2
    third = client.post(PROJECT + "/activation", json=request(release_id, "deploy-c", 2))
    assert third.status_code == 200, third.text
    older = client.get(PROJECT + "/activations?limit=1&before_revision=2")
    assert older.status_code == 200, older.text
    assert older.json()["items"][0]["operation_id"] == "deploy-a"
    assert older.json()["items"][0]["evidence"] == payload["evidence"]
    assert older.json()["next_before_revision"] is None
    detail = client.get(PROJECT + "/activations/deploy-a")
    assert detail.json() == older.json()["items"][0]
    assert not {"content", "content_json", "manifest", "request"}.intersection(detail.json())
    assert "workflows" not in detail.text
    assert runtime.plans == {} and runtime.snapshot_calls == []


def test_omitted_and_null_evidence_preserve_receipt_and_concurrent_retry(api):
    client, _, _, _ = api
    payload = request(release(client))
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: client.post(PROJECT + "/activation", json=payload), range(2))
        )
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert "evidence" not in responses[0].json()
    explicit_null = client.post(PROJECT + "/activation", json={**payload, "evidence": None})
    assert explicit_null.status_code == 200
    assert explicit_null.json() == responses[0].json()
    assert len(client.get(PROJECT + "/activations").json()["items"]) == 1


def test_history_reads_recheck_grants_and_scope_without_provider_calls(api):
    client, backend, data, _ = api
    release_id = release(client)
    assert client.post(PROJECT + "/activation", json=request(release_id)).status_code == 200
    backend.capabilities.available = False
    backend.configurations.available = False
    assert client.get(PROJECT + "/activations").status_code == 200
    assert client.get(PROJECT + "/activations/deploy-a").status_code == 200
    assert client.get(PROJECT + "/activations/missing").status_code == 404
    assert client.get(PROJECT.replace("/dev", "/uat") + "/activations").status_code == 403
    assert client.get(BASE + "/projects/missing/activations").json() == {
        "project_id": "missing",
        "items": [],
        "next_before_revision": None,
    }
    repository, scope, grant, _, _ = data
    repository.revoke_grant(scope, grant.grant_id, expected_revision=1)
    assert client.get(PROJECT + "/activations").status_code == 403
    assert client.get(PROJECT + "/activations/deploy-a").status_code == 403


@pytest.mark.parametrize(
    "evidence",
    [
        {"notes": " "},
        {"change_request_number": ""},
        {"change_request_number": "x" * 129},
        {"notes": "x" * 2001},
        {"actor_id": "forged"},
        {"notes": 123},
    ],
)
def test_invalid_evidence_has_no_activation_side_effect(api, evidence):
    client, _, _, _ = api
    response = client.post(
        PROJECT + "/activation", json=request(release(client), evidence=evidence)
    )
    assert response.status_code == 422, response.text
    assert client.get(PROJECT + "/activation").json()["revision"] == 0
    assert client.get(PROJECT + "/activations").json()["items"] == []
