"""Signed attachment endpoints against real SQLite and identity providers."""

# ruff: noqa: F811

from dataclasses import replace
from hashlib import sha256

import pytest

from nexusflow.tests.api.test_api import BASE, api, publish_activate  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401

PATH = BASE + "/projects/hr/activations/activate-a/attachments"


def upload(client, attachment="approval", content=b"approval", **kwargs):
    return client.put(
        PATH + "/" + attachment,
        content=content,
        headers={"content-type": "application/octet-stream"},
        **kwargs,
    )


def test_optional_opaque_attachment_preserves_deployment_receipt(api):
    client, _, _, _ = api
    publish_activate(client)
    before = client.get(BASE + "/projects/hr/activation").json()
    operation = client.get(BASE + "/projects/hr/activations/activate-a").json()
    assert client.get(PATH).json() == []
    data = b"\x00<svg>private approval</svg>\xff"
    response = upload(client, content=data, params={"display_name": "Approval evidence.pdf"})
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert metadata["attachment_id"] == "approval"
    assert metadata["checksum"] == "sha256:" + sha256(data).hexdigest()
    assert metadata["size"] == len(data)
    assert metadata["actor_id"] == "pairwise-subject"
    assert metadata["display_name"] == "Approval evidence.pdf"
    assert set(metadata) == {
        "attachment_id",
        "checksum",
        "size",
        "actor_id",
        "display_name",
        "created_at",
    }
    assert client.get(PATH).json() == [metadata]
    download = client.get(PATH + "/approval")
    assert download.content == data
    assert download.headers["content-type"] == "application/octet-stream"
    assert (
        download.headers["content-disposition"] == 'attachment; filename="deployment-evidence.bin"'
    )
    assert download.headers["x-content-type-options"] == "nosniff"
    for result in (response, download, client.get(PATH)):
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["x-request-id"]
    assert client.get(BASE + "/projects/hr/activation").json() == before
    assert client.get(BASE + "/projects/hr/activations/activate-a").json() == operation


def test_retry_is_immutable_and_preserves_original_actor(api):
    client, _, identity_data, _ = api
    publish_activate(client)
    first = upload(client)
    _, _, _, _, token = identity_data
    client.headers["authorization"] = "Bearer " + token(sub="another-subject")
    assert upload(client).json() == first.json()
    assert upload(client, content=b"changed").status_code == 409
    assert upload(client, params={"display_name": "Changed"}).status_code == 409
    assert len(client.get(PATH).json()) == 1
    assert client.get(PATH + "/approval").content == b"approval"


@pytest.mark.parametrize("name", ["", " ", "\n", "x" * 129])
def test_invalid_display_names_do_not_echo_private_input(api, name):
    client, _, _, _ = api
    publish_activate(client)
    result = upload(client, params={"display_name": name}, content=b"private-bytes")
    assert result.status_code == 422
    assert "private-bytes" not in result.text
    assert client.get(PATH).json() == []


def test_media_and_body_limits(api):
    client, _, _, _ = api
    publish_activate(client)
    assert client.put(PATH + "/approval", content=b"secret").status_code == 415
    assert upload(client, content=b"").status_code == 422
    large = upload(client, content=b"x" * (2_097_152 + 1))
    assert large.status_code == 413 and large.json()["code"] == "invalid_input"
    assert client.get(PATH).json() == []
    accepted = client.put(
        PATH + "/approval",
        content=b"opaque",
        headers={"content-type": "Application/Octet-Stream; arbitrary=opaque"},
    )
    assert accepted.status_code == 201
    for number in range(1, 10):
        assert upload(client, str(number)).status_code == 201
    assert upload(client, "eleventh").status_code == 409
    assert len(client.get(PATH).json()) == 10


def test_missing_scope_and_fresh_permission_checks(api):
    client, _, identity_data, _ = api
    publish_activate(client)
    assert upload(client).status_code == 201
    assert client.get(PATH + "/missing").status_code == 404
    missing = PATH.replace("activate-a", "missing")
    assert client.get(missing).status_code == 404
    assert (
        client.put(
            missing + "/approval",
            content=b"opaque",
            headers={"content-type": "application/octet-stream"},
        ).status_code
        == 404
    )
    repo, scope, grant, _, _ = identity_data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(replace(grant, scope=uat), expected_revision=0)
    assert client.get(PATH.replace("/dev/", "/uat/")).status_code == 404
    repo.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert client.get(PATH).status_code == 403
    assert client.get(PATH + "/approval").status_code == 403
    # Authorization precedes unsupported media validation.
    assert client.put(PATH + "/approval", content=b"secret").status_code == 403


def test_production_upload_requires_support_role(api):
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
    repo.save_grant(replace(grant, scope=prod, role="production_support"), expected_revision=0)
    base = BASE.replace("/dev", "/prod")
    activated = client.post(
        base + "/projects/hr/activation",
        json={
            "project_id": "hr",
            "release_id": release.release_id,
            "operation_id": "prod-deploy",
            "configuration_revision": "cfg-1",
            "expected_revision": 0,
        },
    )
    assert activated.status_code == 200, activated.text
    path = base + "/projects/hr/activations/prod-deploy/attachments/approval"
    repo.save_grant(replace(grant, scope=prod), expected_revision=1)
    denied = client.put(
        path, content=b"opaque", headers={"content-type": "application/octet-stream"}
    )
    assert denied.status_code == 403
    repo.save_grant(replace(grant, scope=prod, role="production_support"), expected_revision=2)
    allowed = client.put(
        path, content=b"opaque", headers={"content-type": "application/octet-stream"}
    )
    assert allowed.status_code == 201, allowed.text
    assert client.get(path).content == b"opaque"


@pytest.mark.parametrize("attachment", ["bad!", "x" * 129])
def test_invalid_attachment_identifier_is_safely_rejected(api, attachment):
    client, _, _, _ = api
    publish_activate(client)
    response = upload(client, attachment, b"private evidence")
    assert response.status_code == 422
    assert "private evidence" not in response.text
    assert client.get(PATH).json() == []


def test_openapi_binary_request_and_download_and_typed_metadata(api):
    client, _, _, _ = api
    schema = client.app.openapi()
    path = "/api/v1/organizations/{organization_id}/environments/{environment_id}/projects/{project_id}/activations/{operation_id}/attachments/{attachment_id}"
    operation = schema["paths"][path]
    assert operation["put"]["requestBody"]["content"]["application/octet-stream"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    assert operation["put"]["responses"]["201"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/AttachmentMetadata")
    assert (
        operation["get"]["responses"]["200"]["content"]["application/octet-stream"]["schema"][
            "format"
        ]
        == "binary"
    )
