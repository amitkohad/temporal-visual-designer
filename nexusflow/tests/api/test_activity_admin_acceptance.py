"""Independent signed HTTP/SQLite acceptance; worker capability is an explicit fixture."""

# ruff: noqa: F811

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from nexusflow.contracts import Scope
from nexusflow.contracts.models import ActivityRef
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401


def publisher(identity):
    repository, _, grant, _, _ = identity
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)


def upload(client):
    archive, digest = source_bundle()
    response = client.post(
        BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
    )
    assert response.status_code == 201, response.text
    return archive, digest, response.json()


def publish(client, digest):
    response = client.post(BASE + "/activities/publish", json={"artifact_digest": digest})
    assert response.status_code == 201, response.text
    return response.json()


def test_private_import_publish_retry_and_immutable_withdrawn_lookup(api):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    archive, digest, imported = upload(client)
    reference = imported["manifest"]["descriptor"]["activity_ref"]
    assert imported["author_id"] == "pairwise-subject"
    assert imported["state"] == "validated"
    assert client.get(BASE + "/activities").json() == []
    assert client.post(BASE + "/activities/resolve", json=reference).status_code == 404
    assert upload(client)[2] == imported
    assert client.get(BASE + "/activity-imports/" + digest).json() == imported
    downloaded = client.get(BASE + "/activity-imports/" + digest + "/archive")
    assert downloaded.status_code == 200
    assert downloaded.content == archive
    assert "activity-bundle.zip" in downloaded.headers["content-disposition"]
    entry = publish(client, digest)
    assert publish(client, digest) == entry
    assert entry["publisher_id"] == "pairwise-subject"
    scope = identity_data[1]
    ref = ActivityRef.model_validate(reference)
    assert len(backend.catalog.events(scope, ref)) == 1
    transition = {"reference": reference, "target": "withdrawn", "expected_generation": 1}
    withdrawn = client.post(BASE + "/activities/transitions", json=transition)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["generation"] == 2
    assert withdrawn.json()["updated_by"] == "pairwise-subject"
    assert client.post(BASE + "/activities/resolve", json=reference).json() == withdrawn.json()
    assert client.get(BASE + "/activities").json() == []
    assert client.post(BASE + "/activities/transitions", json=transition).json() == withdrawn.json()
    assert len(backend.catalog.events(scope, ref)) == 2


def test_author_cannot_import_publish_or_spoof_publisher_with_token_roles(api):
    client, backend, identity_data, _ = api
    archive, digest = source_bundle()
    client.headers["authorization"] = "Bearer " + identity_data[4](roles=["activity_publisher"])
    assert (
        client.post(
            BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
        ).status_code
        == 403
    )
    assert (
        client.post(BASE + "/activities/publish", json={"artifact_digest": digest}).status_code
        == 403
    )
    assert backend.catalog.by_digest(identity_data[1], digest) is None


def test_publisher_scope_and_fresh_revocation_apply_to_existing_import(api):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    _, digest, _ = upload(client)
    repository, scope, grant, _, _ = identity_data
    uat = Scope(organization_id="acme", environment_id="uat")
    repository.save_grant(replace(grant, scope=uat, role="activity_publisher"), expected_revision=0)
    other_base = BASE.replace("/dev", "/uat")
    assert client.get(other_base + "/activity-imports/" + digest).status_code == 404
    assert (
        client.post(
            other_base + "/activities/publish", json={"artifact_digest": digest}
        ).status_code
        == 404
    )
    repository.revoke_grant(scope, grant.grant_id, expected_revision=2)
    assert client.get(BASE + "/activity-imports/" + digest).status_code == 403
    assert client.get(BASE + "/activity-imports/" + digest + "/archive").status_code == 403
    assert (
        client.post(BASE + "/activities/publish", json={"artifact_digest": digest}).status_code
        == 403
    )
    assert backend.catalog.by_digest(scope, digest) is None


@pytest.mark.parametrize("failure,status", [(False, 422), (True, 503)])
def test_failed_capability_gate_keeps_import_private_and_errors_safe(
    api, monkeypatch, failure, status
):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    _, digest, imported = upload(client)

    def capability(scope, manifest):
        if failure:
            raise OSError("private-worker-secret")
        return False

    monkeypatch.setattr(backend.capabilities, "supports", capability)
    response = client.post(BASE + "/activities/publish", json={"artifact_digest": digest})
    assert response.status_code == status, response.text
    assert "private-worker-secret" not in response.text
    assert backend.catalog.by_digest(identity_data[1], digest) is None
    assert client.get(BASE + "/activity-imports/" + digest).json() == imported
    assert client.get(BASE + "/activities").json() == []


def test_competing_lifecycle_commands_have_one_winner_and_one_audit_event(api):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    _, digest, imported = upload(client)
    publish(client, digest)
    reference = imported["manifest"]["descriptor"]["activity_ref"]

    def transition(target):
        return client.post(
            BASE + "/activities/transitions",
            json={"reference": reference, "target": target, "expected_generation": 1},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(transition, ("deprecated", "withdrawn")))
    assert sorted(response.status_code for response in results) == [200, 409]
    events = backend.catalog.events(identity_data[1], ActivityRef.model_validate(reference))
    assert len(events) == 2


@pytest.mark.parametrize(
    "field,value",
    [("author_id", "attacker"), ("scope", {"organization_id": "other", "environment_id": "prod"})],
)
def test_publish_rejects_untrusted_actor_or_scope_fields(api, field, value):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    _, digest, _ = upload(client)
    response = client.post(
        BASE + "/activities/publish", json={"artifact_digest": digest, field: value}
    )
    assert response.status_code == 422, response.text
    assert backend.catalog.by_digest(identity_data[1], digest) is None
