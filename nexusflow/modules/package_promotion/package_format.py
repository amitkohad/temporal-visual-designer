"""Portable, bounded project archives; inspecting a package never executes its sources."""

import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NoReturn

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from nexusflow.contracts.models import ActivityRef, Contract, Digest, Identifier
from nexusflow.modules.activity_catalog.catalog_types import ExportedActivity
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.modules.package_promotion.types import ActivationError
from nexusflow.modules.project_versioning.canonical import canonical, digest, executable
from nexusflow.modules.project_versioning.types import ProjectContent, Release
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator


@dataclass(frozen=True)
class PackageLimits:
    maximum_archive_bytes: int = 16_777_216
    maximum_expanded_bytes: int = 33_554_432
    maximum_file_bytes: int = 8_388_608
    maximum_project_bytes: int = 4_000_000
    maximum_manifest_bytes: int = 262_144
    maximum_entries: int = 102
    maximum_activities: int = 100
    maximum_source_bytes: int = 33_554_432
    maximum_compression_ratio: int = 100

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Package limits must be positive integers")


class PackageFile(Contract):
    path: str
    size: Annotated[int, Field(ge=0)]
    checksum: Digest
    media_type: Literal["application/json", "application/zip"]


class PackageManifest(Contract):
    package_schema_version: Literal["0.1"]
    organization_id: Identifier
    project_id: Identifier
    release_id: Identifier
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    created_at: Annotated[str, Field(min_length=1, max_length=64)]
    runtime_version: Literal["proof.v1"]
    activity_refs: Annotated[tuple[ActivityRef, ...], Field(max_length=100)]
    content_index: Annotated[tuple[PackageFile, ...], Field(max_length=101)]
    content_digest: Digest
    executable_digest: Digest
    package_digest: Digest

    @field_validator("created_at")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Timestamp must not be blank")
        return value


@dataclass(frozen=True)
class PackageArchive:
    manifest: PackageManifest
    content: ProjectContent
    activities: tuple[ExportedActivity, ...]
    archive: bytes


def _fail(code: str = "invalid_package") -> NoReturn:
    raise ActivationError(code, "Project package failed validation")


def _checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _fail()
        result[key] = value
    return result


def _depth(value: Any, level: int = 0) -> None:
    if level > 32:
        _fail("package_limit")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def _json(data: bytes | str) -> str:
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
    if not isinstance(value, dict):
        _fail()
    _depth(value)
    return canonical(value)


def _key(ref: ActivityRef) -> tuple[str, str, str]:
    return ref.activity_id, ref.version, ref.artifact_digest


def _path(ref: ActivityRef) -> str:
    return "activities/" + ref.artifact_digest.removeprefix("sha256:") + ".zip"


def _validate(
    manifest: PackageManifest,
    content: ProjectContent,
    activities: tuple[ExportedActivity, ...],
    limits: PackageLimits,
    *,
    validated: bool = False,
) -> None:
    if BuiltinContentValidator().validate(content):
        _fail()
    refs = manifest.activity_refs
    if len(refs) > limits.maximum_activities:
        _fail("package_limit")
    if tuple(sorted(refs, key=_key)) != refs or len(
        {(r.activity_id, r.version) for r in refs}
    ) != len(refs):
        _fail()
    required = {_key(ref) for ref in content.activity_refs}
    required.update(
        _key(node.activity_ref)
        for workflow in content.workflows
        for node in workflow.nodes
        if node.activity_ref is not None
    )
    if not required <= {_key(ref) for ref in refs}:
        _fail()
    if tuple(activity.reference for activity in activities) != refs:
        _fail()
    source_bytes = 0
    for activity in activities:
        normalized = (
            activity.manifest_json if validated else validate_bundle(activity.archive).manifest_json
        )
        model = ActivityManifest.model_validate_json(normalized)
        if (
            model.descriptor.activity_ref != activity.reference
            or canonical(
                ActivityManifest.model_validate_json(_json(activity.manifest_json)).model_dump(
                    mode="json"
                )
            )
            != normalized
        ):
            _fail()
        source_bytes += sum(item.size for item in model.files)
        if source_bytes > limits.maximum_source_bytes:
            _fail("package_limit")
    artifacts = [ref.model_dump(mode="json") for ref in refs]
    if manifest.content_digest != digest(
        {"content": content.model_dump(mode="json"), "artifacts": artifacts}
    ):
        _fail()
    if manifest.executable_digest != digest(
        {"content": executable(content), "artifacts": artifacts}
    ):
        _fail()
    if manifest.package_digest != digest(
        manifest.model_dump(mode="json", exclude={"package_digest"})
    ):
        _fail()


def _decode(archive: bytes, limits: PackageLimits) -> PackageArchive:
    if type(archive) is not bytes:
        _fail()
    if len(archive) > limits.maximum_archive_bytes:
        _fail("package_limit")
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        entries = package.infolist()
        if len(entries) > limits.maximum_entries:
            _fail("package_limit")
        seen: set[str] = set()
        expanded = 0
        for entry in entries:
            name = entry.filename
            if (
                entry.orig_filename != name
                or not re.fullmatch(
                    r"(?:manifest\.json|project\.json|activities/[0-9a-f]{64}\.zip)", name
                )
                or name.casefold() in seen
                or entry.is_dir()
            ):
                _fail()
            seen.add(name.casefold())
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG} or mode & 0o111:
                _fail()
            if entry.flag_bits & 1 or entry.compress_type not in {
                zipfile.ZIP_STORED,
                zipfile.ZIP_DEFLATED,
            }:
                _fail()
            bound = limits.maximum_file_bytes
            if name == "manifest.json":
                bound = min(bound, limits.maximum_manifest_bytes)
            elif name == "project.json":
                bound = min(bound, limits.maximum_project_bytes)
            expanded += entry.file_size
            if (
                entry.file_size > bound
                or expanded > limits.maximum_expanded_bytes
                or entry.file_size > max(entry.compress_size, 1) * limits.maximum_compression_ratio
            ):
                _fail("package_limit")
        if not {"manifest.json", "project.json"} <= seen:
            _fail()
        manifest = PackageManifest.model_validate_json(_json(package.read("manifest.json")))
        if len(manifest.activity_refs) > limits.maximum_activities:
            _fail("package_limit")
        expected = {"project.json", *(_path(ref) for ref in manifest.activity_refs)}
        index = manifest.content_index
        if tuple(item.path for item in index) != tuple(sorted(expected)) or seen != expected | {
            "manifest.json"
        }:
            _fail()
        files: dict[str, bytes] = {}
        for item in index:
            entry = package.getinfo(item.path)
            if entry.file_size != item.size:
                _fail()
            expected_media = (
                "application/json" if item.path == "project.json" else "application/zip"
            )
            if item.media_type != expected_media:
                _fail()
            data = package.read(entry)
            if _checksum(data) != item.checksum:
                _fail()
            files[item.path] = data
    content = ProjectContent.model_validate_json(_json(files["project.json"]))
    collected: list[ExportedActivity] = []
    source_bytes = 0
    for ref in manifest.activity_refs:
        data = files[_path(ref)]
        bundle = validate_bundle(data)
        descriptor = ActivityManifest.model_validate_json(bundle.manifest_json)
        source_bytes += sum(item.size for item in descriptor.files)
        if source_bytes > limits.maximum_source_bytes:
            _fail("package_limit")
        collected.append(ExportedActivity(ref, bundle.manifest_json, data))
    activities = tuple(collected)
    _validate(manifest, content, activities, limits, validated=True)
    return PackageArchive(manifest, content, activities, archive)


def decode_package(archive: bytes, limits: PackageLimits | None = None) -> PackageArchive:
    """Validate inventory and nested Python sources without extraction or execution."""
    try:
        return _decode(archive, limits or PackageLimits())
    except ActivationError:
        raise
    except CatalogError as error:
        _fail("package_limit" if error.code == "limit_exceeded" else "invalid_package")
    except (
        ValidationError,
        ValueError,
        UnicodeError,
        RecursionError,
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
        NotImplementedError,
        zlib.error,
        EOFError,
        KeyError,
    ):
        _fail()


def encode_package(
    release: Release, activities: tuple[ExportedActivity, ...], limits: PackageLimits | None = None
) -> PackageArchive:
    """Build deterministic bytes from an immutable release and exact retained artifacts."""
    limits = limits or PackageLimits()
    try:
        if len(release.content_json.encode()) > limits.maximum_project_bytes:
            _fail("package_limit")
        content = ProjectContent.model_validate_json(_json(release.content_json))
        refs = TypeAdapter(tuple[ActivityRef, ...]).validate_json(release.artifacts_json)
        if len(refs) > min(100, limits.maximum_activities) or len(activities) > min(
            100, limits.maximum_activities
        ):
            _fail("package_limit")
        refs = tuple(sorted(refs, key=_key))
        activities = tuple(sorted(activities, key=lambda activity: _key(activity.reference)))
        files = {"project.json": canonical(content.model_dump(mode="json")).encode()}
        files.update({_path(activity.reference): activity.archive for activity in activities})
        # Reject oversized caller-supplied artifacts before hashing their bytes.
        if (
            len(files) + 1 > limits.maximum_entries
            or sum(map(len, files.values())) > limits.maximum_expanded_bytes
            or any(len(data) > limits.maximum_file_bytes for data in files.values())
            or len(files["project.json"]) > limits.maximum_project_bytes
        ):
            _fail("package_limit")
        payload = {
            "package_schema_version": "0.1",
            "organization_id": release.scope.organization_id,
            "project_id": release.project_id,
            "release_id": release.release_id,
            "source_revision": release.source_revision,
            "created_at": release.created_at,
            "runtime_version": "proof.v1",
            "activity_refs": [ref.model_dump(mode="json") for ref in refs],
            "content_index": [
                {
                    "path": path,
                    "size": len(data),
                    "checksum": _checksum(data),
                    "media_type": "application/json"
                    if path == "project.json"
                    else "application/zip",
                }
                for path, data in sorted(files.items())
            ],
            "content_digest": release.content_digest,
            "executable_digest": release.executable_digest,
        }
        payload["package_digest"] = digest(payload)
        manifest = PackageManifest.model_validate_json(canonical(payload))
        files["manifest.json"] = canonical(payload).encode()
        if (
            len(files) > limits.maximum_entries
            or sum(map(len, files.values())) > limits.maximum_expanded_bytes
            or any(len(data) > limits.maximum_file_bytes for data in files.values())
            or len(files["project.json"]) > limits.maximum_project_bytes
            or len(files["manifest.json"]) > limits.maximum_manifest_bytes
        ):
            _fail("package_limit")
        _validate(manifest, content, activities, limits)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as package:
            for path, data in sorted(files.items()):
                entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | 0o600) << 16
                package.writestr(entry, data)
        archive = output.getvalue()
        if len(archive) > limits.maximum_archive_bytes:
            _fail("package_limit")
        return PackageArchive(manifest, content, activities, archive)
    except ActivationError:
        raise
    except CatalogError as error:
        _fail("package_limit" if error.code == "limit_exceeded" else "invalid_package")
    except (ValidationError, ValueError, UnicodeError, RecursionError):
        _fail()
