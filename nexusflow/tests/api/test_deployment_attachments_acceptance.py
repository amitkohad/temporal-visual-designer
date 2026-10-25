"""Independent signed HTTP acceptance for opaque deployment evidence."""

# ruff: noqa: F811
from dataclasses import replace
from hashlib import sha256

from nexusflow.tests.api.test_api import BASE, api, publish_activate  # noqa: F401
from nexusflow.tests.identity.test_service import identity  # noqa: F401

ROOT = BASE + "/projects/hr/activations/activate-a/attachments"
OPAQUE = (
    b"<script>private-evidence</script>\x00\xff\nimport os; raise RuntimeError('never execute')"
)


def upload(client, key="approval", body=OPAQUE, name="Approval evidence", root=ROOT):
    return client.put(
        root + "/" + key,
        params={"display_name": name},
        content=body,
        headers={"content-type": "application/octet-stream"},
    )


def test_opaque_evidence_is_immutable_metadata_only_and_safe_to_download(api):
    client, _, data, _ = api
    publish_activate(client)
    original = client.get(BASE + "/projects/hr/activation").json()
    assert client.get(ROOT).json() == []
    response = upload(client)
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert set(metadata) == {
        "attachment_id",
        "display_name",
        "checksum",
        "size",
        "actor_id",
        "created_at",
    }
    assert metadata["checksum"] == "sha256:" + sha256(OPAQUE).hexdigest()
    assert metadata["size"] == len(OPAQUE)
    assert metadata["actor_id"] == "pairwise-subject"
    assert client.get(ROOT).json() == [metadata]
    assert "private-evidence" not in client.get(ROOT).text
    downloaded = client.get(ROOT + "/approval")
    assert downloaded.content == OPAQUE
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert "deployment-evidence.bin" in downloaded.headers["content-disposition"]
    assert "no-store" in downloaded.headers["cache-control"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    _, _, _, _, token = data
    client.headers["authorization"] = "Bearer " + token(sub="second-author")
    assert upload(client).json() == metadata
    assert upload(client, body=b"changed").status_code == 409
    assert upload(client, name="Different label").status_code == 409
    assert client.get(BASE + "/projects/hr/activation").json() == original


def test_quota_retry_and_invalid_uploads_leave_no_orphans(api):
    client, _, _, _ = api
    publish_activate(client)
    assert upload(client, body=b"").status_code == 422
    assert upload(client, body=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.get(ROOT).json() == []
    receipts = [upload(client, str(i), b"proof").json() for i in range(10)]
    assert len(client.get(ROOT).json()) == 10
    assert upload(client, "overflow", b"proof").status_code == 409
    assert upload(client, "0", b"proof").json() == receipts[0]
    assert len(client.get(ROOT).json()) == 10


def test_chunked_bounds_and_maximum_file(api):
    client, _, _, _ = api
    publish_activate(client)
    maximum = b"x" * (2 * 1024 * 1024)
    assert upload(client, "maximum", maximum).status_code == 201
    for size in (2 * 1024 * 1024 + 1, 6 * 1024 * 1024 + 1):
        response = client.put(
            ROOT + "/large",
            params={"display_name": "Evidence"},
            content=iter([b"x" * size]),
            headers={"content-type": "application/octet-stream"},
        )
        assert response.status_code == 413, response.text
    assert len(client.get(ROOT).json()) == 1


def test_scope_and_fresh_permissions_protect_upload_list_and_download(api):
    client, _, data, _ = api
    publish_activate(client)
    assert upload(client).status_code == 201
    repo, scope, grant, _, token = data
    uat = scope.model_copy(update={"environment_id": "uat"})
    repo.save_grant(replace(grant, scope=uat), expected_revision=0)
    alternate = ROOT.replace("/dev", "/uat")
    assert upload(client, root=alternate).status_code == 404
    assert client.get(alternate).status_code == 404
    assert client.get(alternate + "/approval").status_code == 404
    prod = scope.model_copy(update={"environment_id": "prod"})
    repo.save_grant(replace(grant, scope=prod), expected_revision=0)
    client.headers["authorization"] = "Bearer " + token(roles=["production_support"])
    assert upload(client, root=ROOT.replace("/dev", "/prod")).status_code == 403
    repo.save_grant(replace(grant, role="task_participant"), expected_revision=1)
    assert upload(client).status_code == 403
    assert client.get(ROOT).status_code == 403
    assert client.get(ROOT + "/approval").status_code == 403


def test_missing_operation_never_creates_attachment(api):
    client, _, _, _ = api
    publish_activate(client)
    root = ROOT.replace("activate-a", "missing")
    assert upload(client, root=root).status_code == 404
    assert client.get(root).status_code == 404
    assert client.get(root + "/approval").status_code == 404
    assert client.get(ROOT + "/missing").status_code == 404


def test_provider_failure_is_safe_and_does_not_disclose_bytes(api, monkeypatch):
    client, backend, _, _ = api
    publish_activate(client)

    def unavailable(*args, **kwargs):
        raise OSError("private-database-location-and-secret")

    for method, request in (
        ("put_attachment", lambda: upload(client)),
        ("list_attachments", lambda: client.get(ROOT)),
        ("download_attachment", lambda: client.get(ROOT + "/approval")),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(backend.activations, method, unavailable)
            response = request()
        assert response.status_code == 503, response.text
        assert "private-database" not in response.text
        assert "private-evidence" not in response.text
    assert client.get(ROOT).json() == []
