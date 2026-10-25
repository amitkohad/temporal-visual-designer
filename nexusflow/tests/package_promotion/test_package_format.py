"""Pure portable package tests: no persistence, imports or worker execution."""

import io
import json
import stat
import zipfile
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog_types import ExportedActivity
from nexusflow.modules.activity_catalog.types import ActivityManifest
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.package_promotion.package_format import (
    PackageLimits,
    decode_package,
    encode_package,
)
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.canonical import canonical, digest, executable
from nexusflow.modules.project_versioning.types import ProjectContent, Release
from nexusflow.tests.activity_catalog.test_validation import archive
from nexusflow.tests.project_versioning.test_service import content as workflow_content


def release_with_activity() -> tuple[Release, tuple[ExportedActivity, ...]]:
    data = archive({"src/activity.py": b"def run(context): return {}\n"})
    validated = validate_bundle(data)
    ref = ActivityManifest.model_validate_json(validated.manifest_json).descriptor.activity_ref
    content = ProjectContent.model_validate_json(
        json.dumps(
            {
                "schema_version": "0.1",
                "workflows": json.loads(workflow_content())["workflows"],
                "activity_refs": [ref.model_dump(mode="json")],
                "layout": {"zoom": 1.5},
                "configuration_schema": {"type": "object"},
                "connections": [
                    {
                        "connection_id": "mail",
                        "connector_type": "http",
                        "configuration_schema": {"type": "object"},
                    }
                ],
                "forms": [
                    {
                        "form_id": "approval",
                        "schema_version": "0.1",
                        "data_schema": {"type": "object"},
                        "layout": {"title": "Approve"},
                    }
                ],
            }
        )
    )
    artifacts = [ref.model_dump(mode="json")]
    release = Release(
        Scope(organization_id="acme", environment_id="dev"),
        "hr",
        "release-one",
        2,
        canonical(content.model_dump(mode="json")),
        canonical(artifacts),
        digest({"content": content.model_dump(mode="json"), "artifacts": artifacts}),
        digest({"content": executable(content), "artifacts": artifacts}),
        "actor",
        "2026-10-25T00:00:00Z",
    )
    return release, (ExportedActivity(ref, validated.manifest_json, data),)


def unpack(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return {name: package.read(name) for name in package.namelist()}


def repack(files: dict[str, bytes], compression: int = zipfile.ZIP_STORED) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as package:
        for name, data in reversed(list(files.items())):
            package.writestr(name, data)
    return output.getvalue()


def test_roundtrip_full_resources_exact_nested_archive_and_determinism() -> None:
    release, activities = release_with_activity()
    result = encode_package(release, activities)
    assert result.archive == encode_package(release, activities).archive
    decoded = decode_package(result.archive)
    assert decoded == result
    assert (
        decoded.content.model_dump_json()
        == ProjectContent.model_validate_json(release.content_json).model_dump_json()
    )
    assert decoded.activities == activities
    assert decoded.manifest.organization_id == "acme"
    assert "environment_id" not in decoded.manifest.model_dump()
    assert "actor_id" not in decoded.manifest.model_dump()
    with zipfile.ZipFile(io.BytesIO(result.archive)) as package:
        assert package.namelist() == sorted(package.namelist())
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in package.infolist())


def test_logical_digest_ignores_zip_order_timestamps_compression() -> None:
    release, activities = release_with_activity()
    result = encode_package(release, activities)
    modified = repack(unpack(result.archive), zipfile.ZIP_DEFLATED)
    assert modified != result.archive
    assert decode_package(modified).manifest.package_digest == result.manifest.package_digest


@pytest.mark.parametrize(
    "name", ["../evil", "PROJECT.json", "activities/../evil.zip", "folder/", "extra.json", "a\\b"]
)
def test_unknown_and_unsafe_entries(name: str) -> None:
    release, activities = release_with_activity()
    files = unpack(encode_package(release, activities).archive)
    files[name] = b"unsafe"
    with pytest.raises(ActivationError) as error:
        decode_package(repack(files))
    assert error.value.code == "invalid_package"
    assert "unsafe" not in str(error.value)


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o600, stat.S_IFREG | 0o700, stat.S_IFDIR | 0o600])
def test_special_and_executable_entries(mode: int) -> None:
    release, activities = release_with_activity()
    files = unpack(encode_package(release, activities).archive)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = mode << 16
            package.writestr(entry, data)
    with pytest.raises(ActivationError):
        decode_package(output.getvalue())


@pytest.mark.parametrize(
    "payload", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[]", (b'{"a":' * 34) + b"0" + b"}" * 34]
)
@pytest.mark.parametrize("name", ["manifest.json", "project.json"])
def test_strict_json_before_models(name: str, payload: bytes) -> None:
    release, activities = release_with_activity()
    files = unpack(encode_package(release, activities).archive)
    files[name] = payload
    # Project checksum must match to reach JSON validation.
    if name == "project.json":
        import hashlib

        manifest = json.loads(files["manifest.json"])
        item = next(item for item in manifest["content_index"] if item["path"] == name)
        item.update(size=len(payload), checksum="sha256:" + hashlib.sha256(payload).hexdigest())
        manifest["package_digest"] = digest(
            {key: value for key, value in manifest.items() if key != "package_digest"}
        )
        files["manifest.json"] = canonical(manifest).encode()
    with pytest.raises(ActivationError):
        decode_package(repack(files))


def test_tampering_missing_extra_and_descriptor_mismatch() -> None:
    release, activities = release_with_activity()
    with pytest.raises(ActivationError):
        encode_package(release, ())
    with pytest.raises(ActivationError):
        encode_package(replace(release, artifacts_json="[]"), activities)
    with pytest.raises(ActivationError):
        encode_package(replace(release, content_digest="sha256:" + "0" * 64), activities)
    wrong = replace(
        activities[0], reference=activities[0].reference.model_copy(update={"version": "9.0.0"})
    )
    with pytest.raises(ActivationError):
        encode_package(release, (wrong,))
    files = unpack(encode_package(release, activities).archive)
    files["project.json"] += b" "
    with pytest.raises(ActivationError):
        decode_package(repack(files))


@pytest.mark.parametrize(
    "field",
    [
        "maximum_archive_bytes",
        "maximum_expanded_bytes",
        "maximum_file_bytes",
        "maximum_project_bytes",
        "maximum_manifest_bytes",
        "maximum_entries",
        "maximum_source_bytes",
    ],
)
def test_small_limits(field: str) -> None:
    release, activities = release_with_activity()
    limits = replace(PackageLimits(), **{field: 1})
    with pytest.raises(ActivationError) as error:
        encode_package(release, activities, limits)
    assert error.value.code == "package_limit"


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_limits_are_exact_positive_integers(value: object) -> None:
    with pytest.raises(ValueError):
        PackageLimits(maximum_entries=value)  # type: ignore[arg-type]


def test_oversized_activity_rejected_before_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexusflow.modules.package_promotion import package_format

    release, activities = release_with_activity()
    limits = replace(PackageLimits(), maximum_file_bytes=4096)
    oversized = replace(activities[0], archive=b"x" * 4097)

    def forbidden(data: bytes) -> str:
        pytest.fail("Oversized files must fail before inventory hashing")

    monkeypatch.setattr(package_format, "_checksum", forbidden)
    with pytest.raises(ActivationError) as error:
        encode_package(release, (oversized,), limits)
    assert error.value.code == "package_limit"


def test_duplicate_entries_and_invalid_zip() -> None:
    release, activities = release_with_activity()
    data = encode_package(release, activities).archive
    output = io.BytesIO(data)
    with pytest.warns(UserWarning), zipfile.ZipFile(output, "a") as package:
        package.writestr("project.json", b"{}")
    for invalid in (b"not zip", output.getvalue(), data[:-20]):
        with pytest.raises(ActivationError):
            decode_package(invalid)


def test_aggregate_source_budget_stops_before_next_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexusflow.modules.package_promotion import package_format

    release, activities = release_with_activity()
    first = ActivityManifest.model_validate_json(activities[0].manifest_json)
    descriptor = first.descriptor.model_dump(mode="json")
    descriptor["activity_ref"]["version"] = "2.0.0"
    second = validate_bundle(
        archive({"src/activity.py": b"def run(context): return {}\n"}, descriptor=descriptor)
    )
    second_ref = ActivityManifest.model_validate_json(second.manifest_json).descriptor.activity_ref
    activities += (ExportedActivity(second_ref, second.manifest_json, second.archive),)
    refs = [activity.reference.model_dump(mode="json") for activity in activities]
    content = ProjectContent.model_validate_json(release.content_json)
    release = replace(
        release,
        artifacts_json=canonical(refs),
        content_digest=digest({"content": content.model_dump(mode="json"), "artifacts": refs}),
        executable_digest=digest({"content": executable(content), "artifacts": refs}),
    )
    encoded = encode_package(release, activities)
    calls: list[bytes] = []

    def tracked(data: bytes):  # type: ignore[no-untyped-def]
        calls.append(data)
        return validate_bundle(data)

    monkeypatch.setattr(package_format, "validate_bundle", tracked)
    limits = replace(PackageLimits(), maximum_source_bytes=1)
    for operation in (
        lambda: decode_package(encoded.archive, limits),
        lambda: encode_package(release, activities, limits),
    ):
        calls.clear()
        with pytest.raises(ActivationError) as error:
            operation()
        assert error.value.code == "package_limit"
        assert len(calls) == 1


@pytest.mark.parametrize("corruption", ["missing_workflow", "embedded_environment"])
def test_rehashed_invalid_publication_content_is_rejected(corruption: str) -> None:
    import hashlib

    release, activities = release_with_activity()
    files = unpack(encode_package(release, activities).archive)
    content = json.loads(files["project.json"])
    if corruption == "missing_workflow":
        content["workflows"] = []
    else:
        content["workflows"][0]["nodes"][0]["input_mapping"] = {"base_url": "private-value"}
    project = ProjectContent.model_validate_json(canonical(content))
    refs = json.loads(release.artifacts_json)
    modified = replace(
        release,
        content_json=canonical(content),
        content_digest=digest({"content": project.model_dump(mode="json"), "artifacts": refs}),
        executable_digest=digest({"content": executable(project), "artifacts": refs}),
    )
    with pytest.raises(ActivationError):
        encode_package(modified, activities)
    files["project.json"] = canonical(content).encode()
    manifest = json.loads(files["manifest.json"])
    manifest.update(
        content_digest=modified.content_digest, executable_digest=modified.executable_digest
    )
    item = next(item for item in manifest["content_index"] if item["path"] == "project.json")
    item.update(
        size=len(files["project.json"]),
        checksum="sha256:" + hashlib.sha256(files["project.json"]).hexdigest(),
    )
    manifest["package_digest"] = digest(
        {key: value for key, value in manifest.items() if key != "package_digest"}
    )
    files["manifest.json"] = canonical(manifest).encode()
    with pytest.raises(ActivationError):
        decode_package(repack(files))
