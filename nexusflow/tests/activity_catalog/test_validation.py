"""Adversarial intake checks require no worker, network, or Python execution."""

import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError, IntakeLimits
from nexusflow.modules.activity_catalog.validation import validate_bundle


def archive(files: dict[str, bytes], **changes: Any) -> bytes:
    descriptor = json.loads(
        (Path(__file__).parents[1] / "fixtures/activity-descriptor.json").read_text()
    )
    payload = {
        "schema_version": "0.1",
        "descriptor": descriptor,
        "runtime": {"language": "python", "python_version": "3.12", "entrypoint": "activity:run"},
        "files": [
            {
                "path": path,
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
            for path, data in files.items()
        ],
    }
    payload.update(changes)
    model = ActivityManifest.model_validate_json(json.dumps(payload))
    payload["descriptor"]["activity_ref"]["artifact_digest"] = bundle_digest(model)
    return raw_zip({"manifest.json": json.dumps(payload).encode(), **files})


def raw_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return output.getvalue()


def test_valid_bundle_is_not_executed(tmp_path: Path) -> None:
    target = tmp_path / "must-not-exist"
    code = f"from pathlib import Path\nPath({str(target)!r}).write_text('bad')\ndef run(): pass\n".encode()
    data = archive({"src/activity.py": code})
    result = validate_bundle(data)
    assert result.archive == data
    assert result.digest.startswith("sha256:")
    assert not target.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "/evil.py",
        "C:/evil.py",
        "src\\evil.py",
        "src/CON.py",
        "src/é.py",
        "src/a./x.py",
        "src/./a.py",
    ],
)
def test_unsafe_paths(name: str) -> None:
    with pytest.raises(CatalogError, match="failed intake") as error:
        validate_bundle(raw_zip({name: b"pass"}))
    assert name not in str(error.value)


@pytest.mark.parametrize(
    "code",
    [b"API_KEY = 'sensitive'", b"password: str = 'sensitive'", b"x={'client_secret': 'sensitive'}"],
)
def test_literal_credentials_are_sanitized(code: bytes) -> None:
    with pytest.raises(CatalogError) as error:
        validate_bundle(archive({"src/activity.py": code}))
    assert "sensitive" not in str(error.value)
    assert "sensitive" not in repr(error.value.issues)


def test_dynamic_credentials_allowed_without_execution() -> None:
    validate_bundle(archive({"src/activity.py": b"API_KEY = context.secret('api')"}))


@pytest.mark.parametrize(
    "name", ["setup.py", "sitecustomize.py", "usercustomize.py", "conftest.py", "binary.so"]
)
def test_install_hooks_and_non_python_rejected(name: str) -> None:
    with pytest.raises(CatalogError):
        validate_bundle(archive({"src/activity.py": b"pass", "src/" + name: b"pass"}))


@pytest.mark.parametrize(
    "limits",
    [
        IntakeLimits(maximum_archive_bytes=1),
        IntakeLimits(maximum_expanded_bytes=1),
        IntakeLimits(maximum_file_bytes=1),
        IntakeLimits(maximum_manifest_bytes=1),
        IntakeLimits(maximum_entries=1),
    ],
)
def test_limits(limits: IntakeLimits) -> None:
    with pytest.raises(CatalogError) as error:
        validate_bundle(archive({"src/activity.py": b"pass"}), limits)
    assert error.value.code == "limit_exceeded"


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o644, stat.S_IFREG | 0o755, stat.S_IFIFO | 0o644])
def test_special_and_executable_files(mode: int) -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        entry = zipfile.ZipInfo("src/activity.py")
        entry.external_attr = mode << 16
        bundle.writestr(entry, "pass")
    with pytest.raises(CatalogError):
        validate_bundle(out.getvalue())


@pytest.mark.parametrize("data", [b'{"a":1,"a":2}', b'{"a":NaN}', b"[" * 40 + b"]" * 40])
def test_unsafe_manifest_json(data: bytes) -> None:
    with pytest.raises(CatalogError):
        validate_bundle(raw_zip({"manifest.json": data}))


def test_case_duplicate_rejected() -> None:
    with pytest.raises(CatalogError):
        validate_bundle(raw_zip({"src/a.py": b"pass", "src/A.py": b"pass"}))


def test_corrupt_archive_rejected() -> None:
    with pytest.raises(CatalogError):
        validate_bundle(b"not a zip")


def test_vendored_dependency_ownership() -> None:
    files = {"src/activity.py": b"pass", "src/vendor/example/mod.py": b"pass"}
    with pytest.raises(CatalogError):
        validate_bundle(archive(files))
    runtime = {
        "language": "python",
        "python_version": "3.12",
        "entrypoint": "activity:run",
        "dependencies": [
            {"name": "example", "version": "1.0.0", "files": ["src/vendor/example/mod.py"]}
        ],
    }
    validate_bundle(archive(files, runtime=runtime))


def test_remote_schema_references_rejected() -> None:
    data = archive({"src/activity.py": b"pass"})
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    payload = json.loads(files["manifest.json"])
    payload["descriptor"]["input_schema"] = {"$ref": "https://example.invalid/schema"}
    files["manifest.json"] = json.dumps(payload).encode()
    with pytest.raises(CatalogError):
        validate_bundle(raw_zip(files))


def test_tampered_source_and_missing_entrypoint() -> None:
    data = archive({"src/activity.py": b"pass"})
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    files["src/activity.py"] = b"raise Exception()"
    with pytest.raises(CatalogError):
        validate_bundle(raw_zip(files))
    with pytest.raises(CatalogError):
        validate_bundle(archive({"src/other.py": b"pass"}))


def test_zero_size_directory_entries_are_allowed() -> None:
    data = archive({"src/activity.py": b"pass"})
    output = io.BytesIO(data)
    with zipfile.ZipFile(output, "a") as bundle:
        bundle.mkdir("src/")
    validate_bundle(output.getvalue())


def test_compression_ratio_rejected_before_read() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("src/activity.py", b" " * 100_000)
    with pytest.raises(CatalogError) as error:
        validate_bundle(output.getvalue())
    assert error.value.code == "limit_exceeded"


def test_unsupported_compression() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_BZIP2) as bundle:
        bundle.writestr("src/activity.py", b"pass")
    with pytest.raises(CatalogError) as error:
        validate_bundle(output.getvalue())
    assert error.value.code == "invalid_bundle"


def test_bad_python_syntax_is_incompatible_not_executed() -> None:
    with pytest.raises(CatalogError) as error:
        validate_bundle(archive({"src/activity.py": b"def !!!!"}))
    assert error.value.code == "incompatible_runtime"


def test_extra_source_file_rejected() -> None:
    data = archive({"src/activity.py": b"pass"})
    output = io.BytesIO(data)
    with zipfile.ZipFile(output, "a") as bundle:
        bundle.writestr("src/undeclared.py", b"pass")
    with pytest.raises(CatalogError):
        validate_bundle(output.getvalue())


def test_declared_artifact_digest_is_verified() -> None:
    data = archive({"src/activity.py": b"pass"})
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    payload = json.loads(files["manifest.json"])
    payload["descriptor"]["activity_ref"]["artifact_digest"] = "sha256:" + "b" * 64
    files["manifest.json"] = json.dumps(payload).encode()
    with pytest.raises(CatalogError) as error:
        validate_bundle(raw_zip(files))
    assert error.value.issues[0].path == "/manifest/descriptor/activity_ref/artifact_digest"
