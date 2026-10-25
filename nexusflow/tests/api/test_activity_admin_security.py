"""Independent signed HTTP boundary checks for non-executing private intake."""

# ruff: noqa: F811

import io
import zipfile
from dataclasses import replace

import pytest

from nexusflow.apps.api import create_app
from nexusflow.modules.activity_catalog.types import CatalogError
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.api.test_api import BASE, api, identity  # noqa: F401


def publisher(identity_data):
    repository, _, grant, _, _ = identity_data
    elevated = replace(grant, grant_id="activity-publisher", role="activity_publisher")
    repository.save_grant(elevated, expected_revision=0)
    return elevated


def assert_safe(response, status):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert "sensitive-source" not in response.text


def assert_absent(backend, scope, digest):
    with pytest.raises(CatalogError) as missing:
        backend.intake.get(scope, digest)
    assert missing.value.code == "not_found"


@pytest.mark.parametrize("payload_kind", ["not-zip", "traversal", "manifest"])
def test_rejected_archives_have_no_storage_or_source_side_effects(api, tmp_path, payload_kind):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    marker = tmp_path / "sensitive-source"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef run(v): return v\n"
    archive, digest = source_bundle(source.encode())
    if payload_kind == "not-zip":
        archive = b"sensitive-source: not a ZIP"
    else:
        buffer = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(archive)) as original,
            zipfile.ZipFile(buffer, "w") as changed,
        ):
            for entry in original.infolist():
                data = original.read(entry.filename)
                if payload_kind == "manifest" and entry.filename == "manifest.json":
                    data = b'{"sensitive-source":true}'
                changed.writestr(entry.filename, data)
            if payload_kind == "traversal":
                changed.writestr("../sensitive-source", b"bad")
        archive = buffer.getvalue()
    response = client.post(
        BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
    )
    assert_safe(response, 422)
    assert_absent(backend, identity_data[1], digest)
    assert not marker.exists()


def test_import_publish_and_download_never_execute_source(api, tmp_path):
    client, _, identity_data, _ = api
    publisher(identity_data)
    marker = tmp_path / "sensitive-source"
    archive, digest = source_bundle(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef run(v): return v\n".encode()
    )
    imported = client.post(
        BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["author_id"] == "pairwise-subject"
    published = client.post(BASE + "/activities/publish", json={"artifact_digest": digest})
    assert published.status_code in {200, 201}, published.text
    downloaded = client.get(BASE + f"/activity-imports/{digest}/archive")
    assert downloaded.status_code == 200
    assert downloaded.content == archive
    assert downloaded.headers["content-disposition"] == 'attachment; filename="activity-bundle.zip"'
    assert downloaded.headers["cache-control"] == "no-store"
    assert not marker.exists()


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain"},
        {"content-type": "application/zip", "content-encoding": "gzip"},
    ],
)
def test_unsupported_media_and_encoding_never_store(api, headers):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    archive, digest = source_bundle()
    assert_safe(client.post(BASE + "/activity-imports", content=archive, headers=headers), 415)
    assert_absent(backend, identity_data[1], digest)


def test_business_author_cannot_import_or_read_private_archive(api):
    client, _, identity_data, _ = api
    archive, digest = source_bundle()
    assert_safe(
        client.post(
            BASE + "/activity-imports", content=archive, headers={"content-type": "text/plain"}
        ),
        403,
    )
    for suffix in ("", "/archive"):
        assert_safe(client.get(BASE + f"/activity-imports/{digest}" + suffix), 403)


def test_revocation_and_foreign_environment_cannot_read_import(api):
    client, _, identity_data, _ = api
    repository, scope, _, _, _ = identity_data
    grant = publisher(identity_data)
    archive, digest = source_bundle()
    assert (
        client.post(
            BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
        ).status_code
        == 201
    )
    foreign = replace(
        grant, grant_id="uat-publisher", scope=scope.model_copy(update={"environment_id": "uat"})
    )
    repository.save_grant(foreign, expected_revision=0)
    for suffix in ("", "/archive"):
        assert_safe(
            client.get(BASE.replace("/dev", "/uat") + f"/activity-imports/{digest}" + suffix), 404
        )
    repository.revoke_grant(scope, grant.grant_id, expected_revision=grant.revision)
    for suffix in ("", "/archive"):
        assert_safe(client.get(BASE + f"/activity-imports/{digest}" + suffix), 403)


def test_storage_errors_are_sanitized(api, monkeypatch):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    archive, digest = source_bundle()

    def unavailable(*args):
        raise OSError("sensitive-source provider path and credentials")

    monkeypatch.setattr(backend.intake, "put", unavailable)
    assert_safe(
        client.post(
            BASE + "/activity-imports", content=archive, headers={"content-type": "application/zip"}
        ),
        503,
    )
    for method, suffix in (("get", ""), ("archive", "/archive")):
        monkeypatch.setattr(backend.intake, method, unavailable)
        assert_safe(client.get(BASE + f"/activity-imports/{digest}" + suffix), 503)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_global_declared_and_streamed_upload_limits_do_not_store(api, declared):
    client, backend, identity_data, _ = api
    publisher(identity_data)
    archive, digest = source_bundle()
    app = create_app(backend)
    headers = [
        (b"authorization", client.headers["authorization"].encode()),
        (b"content-type", b"application/zip"),
    ]
    if declared:
        headers.append((b"content-length", b"6000001"))
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * 3_000_001, "more_body": True},
            {"type": "http.request", "body": b"x" * 3_000_001, "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": BASE + "/activity-imports",
            "raw_path": (BASE + "/activity-imports").encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("test", 123),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
    assert_absent(backend, identity_data[1], digest)
