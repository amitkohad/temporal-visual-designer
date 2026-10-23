"""Versioned source-bundle contracts and trusted intake ports."""

from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import Field

from nexusflow.contracts.models import ActivityDescriptor, Contract, Digest, Identifier, Scope
from nexusflow.contracts.validation import ValidationIssue


class BundleFile(Contract):
    path: Annotated[str, Field(min_length=1, max_length=240)]
    digest: Digest
    size: Annotated[int, Field(ge=0, le=16_777_216)]


class PythonDependency(Contract):
    name: Identifier
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    files: Annotated[tuple[str, ...], Field(min_length=1, max_length=200)]


class PythonRuntime(Contract):
    language: Literal["python"]
    python_version: Literal["3.12", "3.13"]
    entrypoint: Annotated[
        str,
        Field(
            pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$",
            max_length=200,
        ),
    ]
    dependencies: Annotated[tuple[PythonDependency, ...], Field(max_length=100)] = ()


class ActivityManifest(Contract):
    schema_version: Literal["0.1"]
    descriptor: ActivityDescriptor
    runtime: PythonRuntime
    files: Annotated[tuple[BundleFile, ...], Field(min_length=1, max_length=200)]


@dataclass(frozen=True)
class IntakeLimits:
    maximum_archive_bytes: int = 8_388_608
    maximum_expanded_bytes: int = 16_777_216
    maximum_file_bytes: int = 1_048_576
    maximum_manifest_bytes: int = 262_144
    maximum_entries: int = 256
    maximum_compression_ratio: int = 100

    def __post_init__(self) -> None:
        for value in vars(self).values():
            if type(value) is not int or value < 1:
                raise ValueError("Intake limits must be positive integers")


@dataclass(frozen=True)
class ValidatedBundle:
    digest: str
    manifest_json: str
    archive: bytes


@dataclass(frozen=True)
class ImportedBundle:
    scope: Scope
    digest: str
    manifest_json: str
    author_id: str
    created_at: str
    state: Literal["validated"] = "validated"


class CatalogError(Exception):
    def __init__(self, code: str, message: str, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(message)


class CatalogAuthorization(Protocol):
    def authorize(self, scope: Scope, permission: str) -> str:
        """Freshly authorize the requested scope and return the trusted actor ID."""
        ...


class IntakeRepository(Protocol):
    def put(self, scope: Scope, bundle: ValidatedBundle, author_id: str) -> ImportedBundle: ...
    def get(self, scope: Scope, digest: str) -> ImportedBundle: ...
    def archive(self, scope: Scope, digest: str) -> bytes: ...
