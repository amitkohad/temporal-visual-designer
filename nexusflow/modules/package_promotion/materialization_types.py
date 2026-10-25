"""Durable destination package materialization contracts and repository port."""

import hashlib
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field

from nexusflow.contracts.models import Contract, Digest, Identifier, Scope
from nexusflow.modules.package_promotion.package_format import PackageManifest
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import ProjectRecord, Release


class MaterializationRequest(Contract):
    operation_id: Identifier
    expected_project_revision: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    configuration_revision: Identifier
    runtime_version: Literal["proof.v1"] = "proof.v1"


class MaterializationReceipt(Contract):
    operation_id: Identifier
    import_operation_id: Identifier
    project_id: Identifier
    package_digest: Digest
    source_release_id: Identifier
    source_revision: Annotated[int, Field(ge=1, lt=2**63)]
    release_id: Identifier
    revision: Annotated[int, Field(ge=1, lt=2**63)]
    configuration_revision: Identifier
    runtime_version: Literal["proof.v1"]
    actor_id: str
    created_at: str
    state: Literal["materialized"] = "materialized"


@dataclass(frozen=True)
class MaterializationRecord:
    request: MaterializationRequest
    import_operation_id: str
    package_digest: str
    receipt: MaterializationReceipt
    release: Release


def materialization_release_id(scope: Scope, operation_id: str) -> str:
    identity = canonical([scope.organization_id, scope.environment_id, operation_id])
    return "import-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


@runtime_checkable
class MaterializationRepository(Protocol):
    def project(self, scope: Scope, project_id: str) -> ProjectRecord: ...

    def materialization_receipt(
        self, scope: Scope, operation_id: str
    ) -> MaterializationRecord | None: ...

    def commit_materialization(
        self,
        scope: Scope,
        import_operation_id: str,
        request: MaterializationRequest,
        manifest: PackageManifest,
        release: Release,
    ) -> MaterializationRecord: ...
