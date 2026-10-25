"""Signed HTTP activity administration against actual scoped SQLite storage."""

from dataclasses import replace

import pytest

from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401


@pytest.fixture
def publisher(api):  # noqa: F811
    client, backend, identity_data, runtime = api
    repository, scope, grant, _, _ = identity_data
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
    return client, backend, identity_data, runtime


def upload(client):
    archive, digest = source_bundle()
    response = client.post(
        BASE + "/activity-imports",
        content=archive,
        headers={"content-type": "Application/Zip; charset=binary"},
    )
    assert response.status_code == 201, response.text
    return archive, digest, response.json()


def test_import_download_publish_and_lifecycle(publisher):
    client, _, _, _ = publisher
    archive, digest, imported = upload(client)
    assert imported["digest"] == digest
    assert imported["state"] == "validated"
    assert imported["author_id"] == "pairwise-subject"
    assert set(imported) == {"digest", "manifest", "state", "author_id", "created_at"}
    assert client.get(BASE + "/activity-imports/" + digest).json() == imported
    download = client.get(BASE + "/activity-imports/" + digest + "/archive")
    assert download.content == archive
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"] == 'attachment; filename="activity-bundle.zip"'
    assert download.headers["cache-control"] == "no-store"
    assert upload(client)[2] == imported
    published = client.post(BASE + "/activities/publish", json={"artifact_digest": digest})
    assert published.status_code == 201, published.text
    entry = published.json()
    assert (
        client.post(BASE + "/activities/publish", json={"artifact_digest": digest}).json() == entry
    )
    reference = imported["manifest"]["descriptor"]["activity_ref"]
    assert client.post(BASE + "/activities/resolve", json=reference).json() == entry
    assert len(client.get(BASE + "/activities").json()) == 1
    transition = {"reference": reference, "target": "withdrawn", "expected_generation": 1}
    withdrawn = client.post(BASE + "/activities/transitions", json=transition)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "withdrawn"
    assert client.post(BASE + "/activities/resolve", json=reference).json() == withdrawn.json()
    assert client.get(BASE + "/activities").json() == []
    assert client.post(BASE + "/activities/transitions", json=transition).json() == withdrawn.json()
    transition["target"] = "deprecated"
    assert client.post(BASE + "/activities/transitions", json=transition).status_code == 409


@pytest.mark.parametrize(
    "media", [None, "application/json", "multipart/form-data", "application/octet-stream"]
)
def test_unsupported_media_is_safe(publisher, media):
    client, _, _, _ = publisher
    headers = {} if media is None else {"content-type": media}
    response = client.post(BASE + "/activity-imports", content=b"private-payload", headers=headers)
    assert response.status_code == 415
    assert "private-payload" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_import_authorization_precedes_media_validation_and_is_fresh(api):  # noqa: F811
    client, _, identity_data, _ = api
    assert client.post(BASE + "/activity-imports", content=b"bad").status_code == 403
    repository, _, grant, _, _ = identity_data
    repository.save_grant(replace(grant, role="activity_publisher"), expected_revision=1)
    _, digest, _ = upload(client)
    repository.save_grant(replace(grant, role="business_author"), expected_revision=2)
    assert client.get(BASE + "/activity-imports/" + digest).status_code == 403
    assert client.get(BASE + "/activity-imports/" + digest + "/archive").status_code == 403
    assert (
        client.post(BASE + "/activities/publish", json={"artifact_digest": digest}).status_code
        == 403
    )
    del client.headers["authorization"]
    assert client.post(BASE + "/activity-imports", content=b"bad").status_code == 401


def test_invalid_bundle_missing_digest_and_strict_payloads(publisher):
    client, _, _, _ = publisher
    invalid = client.post(
        BASE + "/activity-imports",
        content=b"bad-zip-private",
        headers={"content-type": "application/zip"},
    )
    assert invalid.status_code == 422 and "bad-zip-private" not in invalid.text
    assert client.get(BASE + "/activity-imports/sha256:" + "0" * 64).status_code == 404
    _, digest, imported = upload(client)
    assert (
        client.post(
            BASE + "/activities/publish", json={"artifact_digest": digest, "actor_id": "forged"}
        ).status_code
        == 422
    )
    reference = imported["manifest"]["descriptor"]["activity_ref"]
    for generation in [True, "1", 0, 2**63]:
        response = client.post(
            BASE + "/activities/transitions",
            json={"reference": reference, "target": "withdrawn", "expected_generation": generation},
        )
        assert response.status_code == 422
    assert (
        client.post(BASE + "/activities/resolve", json={**reference, "scope": {}}).status_code
        == 422
    )


def test_binary_openapi_contract(publisher):
    client, _, _, _ = publisher
    schema = client.get(BASE + "/schema").json()
    prefix = "/api/v1/organizations/{organization_id}/environments/{environment_id}"
    upload_schema = schema["paths"][prefix + "/activity-imports"]["post"]
    assert upload_schema["requestBody"]["content"] == {
        "application/zip": {"schema": {"type": "string", "format": "binary"}}
    }
    assert "201" in upload_schema["responses"]
    download_schema = schema["paths"][prefix + "/activity-imports/{digest}/archive"]["get"]
    assert set(download_schema["responses"]["200"]["content"]) == {"application/zip"}
