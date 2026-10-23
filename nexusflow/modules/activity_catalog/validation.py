"""Bounded, non-executing source intake. Credential heuristics are not a DLP guarantee."""

import ast
import hashlib
import io
import json
import re
import stat
import zipfile
import zlib
from typing import Any, NoReturn

from pydantic import ValidationError

from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.types import (
    ActivityManifest,
    CatalogError,
    IntakeLimits,
    ValidatedBundle,
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _fail(code: str = "invalid_bundle", path: str = "/archive") -> NoReturn:
    raise CatalogError(
        code,
        "Activity bundle failed intake validation",
        (ValidationIssue(path, code, "Invalid or unsupported bundle content"),),
    )


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            _fail(path="/manifest")
        result[key] = value
    return result


def _depth(value: Any, level: int = 0) -> None:
    if level > 32:
        _fail("limit_exceeded", "/manifest")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def _path(name: str) -> None:
    if not name or not name.isascii() or "\\" in name or "\x00" in name:
        _fail()
    parts = (name[:-1] if name.endswith("/") else name).split("/")
    for part in parts:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in {".", ".."}:
            _fail()
        if part.endswith((".", " ")) or re.fullmatch(
            r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part
        ):
            _fail()


def _no_refs(value: Any) -> None:
    if isinstance(value, dict):
        if "$ref" in value or "$dynamicRef" in value:
            _fail(path="/manifest/descriptor")
        for child in value.values():
            _no_refs(child)
    elif isinstance(value, list):
        for child in value:
            _no_refs(child)


def _source(data: bytes, python_version: str) -> None:
    try:
        tree = ast.parse(data, feature_version=(3, int(python_version.split(".")[1])))
    except (SyntaxError, ValueError, UnicodeError, RecursionError):
        _fail("incompatible_runtime")
        return
    secret = re.compile(r"(?i)^(?:api_?key|password|client_?secret|access_?token|secret_?key)$")

    def literal(value: ast.AST) -> bool:
        return (
            isinstance(value, ast.Constant) and isinstance(value.value, str) and bool(value.value)
        )

    def sensitive(target: ast.AST) -> bool:
        name = (
            target.id
            if isinstance(target, ast.Name)
            else (target.attr if isinstance(target, ast.Attribute) else "")
        )
        return bool(secret.fullmatch(name))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and literal(node.value):
            if any(sensitive(target) for target in node.targets):
                _fail()
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            if sensitive(node.target) and literal(node.value):
                _fail()
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if secret.fullmatch(key.value) and literal(value):
                        _fail()


def validate_bundle(archive: bytes, limits: IntakeLimits | None = None) -> ValidatedBundle:
    """Inspect bytes only; never extract, install, import, or execute sources."""
    limits = limits or IntakeLimits()
    if type(archive) is not bytes:
        _fail()
    if len(archive) > limits.maximum_archive_bytes:
        _fail("limit_exceeded")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) > limits.maximum_entries:
                _fail("limit_exceeded")
            seen: set[str] = set()
            file_paths: set[str] = set()
            directory_paths: set[str] = set()
            files: dict[str, bytes] = {}
            expanded = 0
            for entry in entries:
                name = entry.filename
                _path(name)
                # ZipInfo truncates NUL names, so inspect the original name too.
                if entry.orig_filename != name:
                    _fail()
                folded = name.rstrip("/").casefold()
                if folded in seen:
                    _fail()
                seen.add(folded)
                parts = folded.split("/")
                ancestors = {"/".join(parts[:index]) for index in range(1, len(parts))}
                if ancestors & file_paths or (entry.is_dir() and folded in file_paths):
                    _fail()
                if not entry.is_dir() and folded in directory_paths:
                    _fail()
                directory_paths.update(ancestors)
                if entry.is_dir():
                    directory_paths.add(folded)
                else:
                    file_paths.add(folded)
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or (
                    not entry.is_dir() and mode & 0o111
                ):
                    _fail()
                if entry.flag_bits & 1 or entry.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }:
                    _fail()
                expanded += entry.file_size
                bound = (
                    limits.maximum_manifest_bytes
                    if name == "manifest.json"
                    else limits.maximum_file_bytes
                )
                if (
                    entry.file_size > bound
                    or expanded > limits.maximum_expanded_bytes
                    or entry.file_size
                    > max(entry.compress_size, 1) * limits.maximum_compression_ratio
                ):
                    _fail("limit_exceeded")
                if entry.is_dir():
                    if entry.file_size or kind == stat.S_IFREG:
                        _fail()
                    continue
                if kind == stat.S_IFDIR:
                    _fail()
                if name != "manifest.json":
                    if not name.startswith("src/") or not name.endswith(".py"):
                        _fail()
                    if name.rsplit("/", 1)[-1].casefold() in {
                        "setup.py",
                        "sitecustomize.py",
                        "usercustomize.py",
                        "conftest.py",
                    }:
                        _fail()
                files[name] = bundle.read(entry)
    except CatalogError:
        raise
    except (
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
        ValueError,
        NotImplementedError,
        zlib.error,
        EOFError,
    ):
        _fail()
    if "manifest.json" not in files:
        _fail(path="/manifest")
    try:
        payload = json.loads(
            files.pop("manifest.json"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: _fail(path="/manifest"),
        )
        _depth(payload)
        manifest = ActivityManifest.model_validate_json(canonical(payload))
    except CatalogError:
        raise
    except (ValueError, UnicodeError, RecursionError, ValidationError):
        _fail(path="/manifest")
    expected: set[str] = set()
    for item in manifest.files:
        _path(item.path)
        if item.path in expected or item.path not in files:
            _fail(path="/manifest/files")
        expected.add(item.path)
        data = files[item.path]
        if len(data) != item.size or "sha256:" + hashlib.sha256(data).hexdigest() != item.digest:
            _fail(path="/manifest/files")
    if expected != files.keys():
        _fail(path="/manifest/files")
    module = manifest.runtime.entrypoint.split(":")[0]
    if "src/" + module.replace(".", "/") + ".py" not in files:
        _fail(path="/manifest/runtime/entrypoint")
    assigned: set[str] = set()
    dependencies: set[str] = set()
    for dependency in manifest.runtime.dependencies:
        key = re.sub(r"[-_.]+", "_", dependency.name.casefold())
        if key in dependencies:
            _fail(path="/manifest/runtime/dependencies")
        dependencies.add(key)
        for path in dependency.files:
            if (
                path in assigned
                or path not in files
                or not path.startswith("src/vendor/" + dependency.name + "/")
            ):
                _fail(path="/manifest/runtime/dependencies")
            assigned.add(path)
    if assigned != {path for path in files if path.startswith("src/vendor/")}:
        _fail(path="/manifest/runtime/dependencies")
    for schema in (
        manifest.descriptor.input_schema,
        manifest.descriptor.output_schema,
        manifest.descriptor.configuration_schema,
    ):
        _no_refs(schema)
    for data in files.values():
        _source(data, manifest.runtime.python_version)
    digest = bundle_digest(manifest)
    if manifest.descriptor.activity_ref.artifact_digest != digest:
        _fail(path="/manifest/descriptor/activity_ref/artifact_digest")
    return ValidatedBundle(digest, canonical(manifest.model_dump(mode="json")), archive)
