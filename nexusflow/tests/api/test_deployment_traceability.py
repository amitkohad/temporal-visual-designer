"""Signed HTTP deployment receipts over the actual scoped SQLite providers."""

# Imported pytest fixture names are deliberately reused by test parameters.
# ruff: noqa: F811

from dataclasses import replace

import pytest

from nexusflow.tests.api.test_api import BASE, api, publish_activate  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401


def payload(release, revision=1, operation="deploy-b", **updates):
    return {
        "project_id": "hr",
        "release_id": release,
        "operation_id": operation,
        "configuration_revision": "cfg-1",
        "expected_revision": revision,
        **updates,
    }


def test_optional_evidence_is_compatible_and_visible_on_receipts(api):
    client, _, _, _ = api
    empty = client.get(BASE + "/projects/hr/activation").json()
    assert empty["revision"] == 0 and "evidence" not in empty
    release = publish_activate(client)
    assert "evidence" not in client.get(BASE + "/projects/hr/activation").json()
    evidence = {"change_request_number": "CHG-2026-001", "notes": "Approved offline"}
    response = client.post(
        BASE + "/projects/hr/activation", json=payload(release, evidence=evidence)
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["evidence"] == evidence
    assert client.get(BASE + "/projects/hr/activation").json() == receipt
    operation = client.get(BASE + "/projects/hr/activations/deploy-b")
    assert operation.status_code == 200, operation.text
    assert operation.json()["evidence"] == evidence
    assert operation.json()["action"] == "activate"
    assert operation.json()["actor_id"] == "pairwise-subject"
    history = client.get(BASE + "/projects/hr/activations").json()
    assert [item["revision"] for item in history["items"]] == [2, 1]
    assert history["items"][0] == operation.json()
    assert not {"content", "content_json", "request", "release", "scope"} & operation.json().keys()


def test_retry_retains_original_actor_without_duplicate_audit_and_changed_evidence_conflicts(api):
    client, backend, identity_data, _ = api
    release = publish_activate(client)
    body = payload(release, evidence={"change_request_number": "CHG-1"})
    first = client.post(BASE + "/projects/hr/activation", json=body)
    assert first.status_code == 200, first.text
    _, scope, _, _, token = identity_data
    count = len(backend.activations.events(scope, "hr"))
    client.headers["authorization"] = "Bearer " + token(sub="different-pairwise-subject")
    retry = client.post(BASE + "/projects/hr/activation", json=body)
    assert retry.json() == first.json()
    assert len(backend.activations.events(scope, "hr")) == count
    changed = client.post(
        BASE + "/projects/hr/activation",
        json={**body, "evidence": {"change_request_number": "CHG-2"}},
    )
    assert changed.status_code == 409
    assert client.get(BASE + "/projects/hr/activation").json() == first.json()


def test_keyset_history_excludes_concurrently_appended_activation(api):
    client, _, _, _ = api
    release = publish_activate(client)
    assert client.post(BASE + "/projects/hr/activation", json=payload(release)).status_code == 200
    first = client.get(BASE + "/projects/hr/activations?limit=1").json()
    assert first["next_before_revision"] == 2
    assert (
        client.post(
            BASE + "/projects/hr/activation",
            json=payload(release, 2, "deploy-c", action="rollback"),
        ).status_code
        == 200
    )
    second = client.get(BASE + "/projects/hr/activations?limit=1&before_revision=2").json()
    assert [item["revision"] for item in second["items"]] == [1]
    assert second["next_before_revision"] is None
    latest = client.get(BASE + "/projects/hr/activations/deploy-c").json()
    assert latest["action"] == "rollback" and latest["revision"] == 3


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "before_revision=0", f"before_revision={2**63}"]
)
def test_history_rejects_invalid_bounds(api, query):
    client, _, _, _ = api
    assert client.get(BASE + "/projects/hr/activations?" + query).status_code == 422


@pytest.mark.parametrize(
    "evidence",
    [
        {"change_request_number": " "},
        {"notes": "\n"},
        {"change_request_number": "x" * 129},
        {"notes": "x" * 2001},
        {"actor_id": "forged"},
        {"attachments": ["untrusted"]},
        {"notes": 10},
    ],
)
def test_evidence_rejects_invalid_or_forged_fields_without_writes(api, evidence):
    client, _, _, _ = api
    release = publish_activate(client)
    response = client.post(
        BASE + "/projects/hr/activation", json=payload(release, evidence=evidence)
    )
    assert response.status_code == 422
    assert client.get(BASE + "/projects/hr/activation").json()["revision"] == 1


def test_history_scope_missing_operation_and_fresh_revocation(api):
    client, _, identity_data, _ = api
    publish_activate(client)
    repo, scope, grant, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(replace(grant, scope=uat), expected_revision=0)
    other = BASE.replace("/dev", "/uat")
    assert client.get(other + "/projects/hr/activations").json()["items"] == []
    assert client.get(other + "/projects/hr/activations/activate-a").status_code == 404
    assert client.get(BASE + "/projects/missing/activations").json()["items"] == []
    assert client.get(BASE + "/projects/hr/activations/missing").status_code == 404
    repo.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(BASE + "/projects/hr/activations").status_code == 403
    assert client.get(BASE + "/projects/hr/activations/activate-a").status_code == 403


def test_openapi_describes_evidence_and_metadata_only_history(api):
    client, _, _, _ = api
    schema = client.app.openapi()
    models = schema["components"]["schemas"]
    assert "evidence" in models["ActivationRequest"]["properties"]
    assert "evidence" in models["ProjectActivation"]["properties"]
    assert "items" in models["ActivationHistory"]["properties"]
    assert "action" in models["ActivationSummary"]["properties"]
    assert models["DeploymentEvidence"]["additionalProperties"] is False


def test_production_support_can_deploy_with_optional_traceability(api):
    client, backend, identity_data, _ = api
    dev_release = publish_activate(client)
    repo, scope, grant, _, _ = identity_data
    prod = scope.model_copy(update={"environment_id": "prod"})
    source = backend.projects.release(scope, "hr", dev_release)
    backend.projects.create(prod, "hr", "HR", source.content_json, "Imported", "importer")
    release = backend.projects.publish(
        prod,
        "hr",
        1,
        "import",
        source.artifacts_json,
        source.content_digest,
        source.executable_digest,
        "importer",
    )
    repo.save_grant(replace(grant, scope=prod), expected_revision=0)
    url = BASE.replace("/dev", "/prod") + "/projects/hr/activation"
    body = payload(release.release_id, 0, "prod-deploy", evidence={"notes": "Offline approval"})
    assert client.post(url, json=body).status_code == 403
    repo.save_grant(replace(grant, scope=prod, role="production_support"), expected_revision=1)
    deployed = client.post(url, json=body)
    assert deployed.status_code == 200, deployed.text
    assert deployed.json()["evidence"]["notes"] == "Offline approval"
    assert client.get(url + "s/prod-deploy").status_code == 200
