"""Authorized whole-project activation, rollback and internal start resolution."""

import hashlib
import json
import sqlite3

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import ActivityRef, Identifier, Scope
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationEvent,
    ActivationReadiness,
    ActivationRecord,
    ActivationRequest,
)
from nexusflow.modules.project_versioning.canonical import digest, executable
from nexusflow.modules.project_versioning.types import (
    ProjectAuthorization,
    ProjectContent,
    ProjectRepository,
    Release,
    VersioningError,
)
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator


def validate_release(release: Release, scope: Scope, request: ActivationRequest) -> None:
    """Recheck the immutable source snapshot before any holds or activation."""
    if (release.scope, release.project_id, release.release_id) != (
        scope,
        request.project_id,
        request.release_id,
    ):
        raise ActivationError("invalid_release", "Release does not belong to the target")
    try:
        if (
            len(release.content_json.encode()) > 4_000_000
            or len(release.artifacts_json.encode()) > 1_000_000
        ):
            raise ValueError
        content = ProjectContent.model_validate_json(release.content_json)
        if BuiltinContentValidator().validate(content):
            raise ValueError
        refs = TypeAdapter(tuple[ActivityRef, ...]).validate_json(release.artifacts_json)
        keys = [(ref.activity_id, ref.version) for ref in refs]
        if not content.workflows or len(refs) > 1000 or len(set(keys)) != len(keys):
            raise ValueError
        required = set(ref.model_dump_json() for ref in content.activity_refs)
        required.update(
            node.activity_ref.model_dump_json()
            for workflow in content.workflows
            for node in workflow.nodes
            if node.activity_ref is not None
        )
        if not required <= {ref.model_dump_json() for ref in refs}:
            raise ValueError
        artifacts = json.loads(release.artifacts_json)
        if release.content_digest != digest(
            {"content": content.model_dump(mode="json"), "artifacts": artifacts}
        ):
            raise ValueError
        if release.executable_digest != digest(
            {"content": executable(content), "artifacts": artifacts}
        ):
            raise ValueError
    except (ValidationError, ValueError, UnicodeError, RecursionError):
        raise ActivationError("invalid_release", "Release integrity check failed") from None


class ActivationService:
    def __init__(
        self,
        repository: SQLiteActivationRepository,
        releases: ProjectRepository,
        authorization: ProjectAuthorization,
        readiness: ActivationReadiness,
    ) -> None:
        self.repository = repository
        self.releases = releases
        self.authorization = authorization
        self.readiness = readiness

    def apply(self, scope: Scope, request: ActivationRequest) -> ActivationRecord:
        actor = self.authorization.authorize(scope, "package." + request.action, request.project_id)
        checksum = ""
        try:
            previous = self.repository.receipt(scope, request)
            if previous is not None:
                return previous
            current = self.repository.current(scope, request.project_id)
            if (0 if current is None else current.revision) != request.expected_revision:
                # A duplicate may have committed after the first receipt read.
                previous = self.repository.receipt(scope, request)
                if previous is not None:
                    return previous
                raise ActivationError("conflict", "Activation revision has changed")
            retained = self.repository.retained(scope, request.project_id, request.release_id)
            if request.action == "rollback" and retained is None:
                raise ActivationError(
                    "not_found", "Rollback requires a previously activated release"
                )
            release = self.releases.release(scope, request.project_id, request.release_id)
            validate_release(release, scope, request)
            checksum = release.executable_digest
            if retained is not None and release != retained:
                raise ActivationError("conflict", "An activated release cannot change")
            issues = self.readiness.validate(scope, release, request)
            if issues:
                raise ActivationError(
                    "incompatible_release", "Destination is not ready for this release", issues
                )
            # Acquire conservative permanent holds before changing the pointer.
            # Cross-provider failure may leak holds, but cannot activate without them.
            usage = (
                "activation-"
                + hashlib.sha256(
                    json.dumps(
                        [
                            scope.organization_id,
                            scope.environment_id,
                            request.project_id,
                            request.operation_id,
                        ],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            self.releases.retain(scope, request.project_id, release.release_id, usage)
            self.readiness.retain(scope, release, usage)
            return self.repository.commit(scope, request, release, actor)
        except ActivationError as error:
            self.repository.rejected(scope, request, actor, error.code, checksum)
            raise
        except VersioningError as error:
            code = "not_found" if error.code == "not_found" else "unavailable"
            self.repository.rejected(scope, request, actor, code, checksum)
            raise ActivationError(code, "Target release is unavailable") from None
        except (OSError, RuntimeError, sqlite3.Error):
            # If commit succeeded but its response was lost, the original receipt
            # is authoritative on retry; never compensate by undoing activation.
            try:
                if self.repository.receipt(scope, request) is None:
                    self.repository.rejected(scope, request, actor, "unavailable", checksum)
            except (OSError, RuntimeError, sqlite3.Error, ActivationError):
                pass  # Unavailable storage cannot guarantee a failure audit write.
            raise ActivationError(
                "unavailable", "Activation status is uncertain; retry the same operation"
            ) from None

    def current(self, scope: Scope, project_id: str) -> ActivationRecord | None:
        self.authorization.authorize(scope, "run.read", project_id)
        TypeAdapter(Identifier).validate_python(project_id)
        return self.repository.current(scope, project_id)

    def events(self, scope: Scope, project_id: str) -> tuple[ActivationEvent, ...]:
        self.authorization.authorize(scope, "run.read", project_id)
        TypeAdapter(Identifier).validate_python(project_id)
        return self.repository.events(scope, project_id)


class ActivatedReleaseResolver:
    """Trusted internal adapter; StartService authorizes before invoking this port."""

    def __init__(self, repository: SQLiteActivationRepository) -> None:
        self.repository = repository

    def resolve(
        self, scope: Scope, project_id: str, workflow_id: str
    ) -> tuple[ReleaseBinding, str]:
        record = self.repository.current(scope, project_id)
        if record is None:
            raise ActivationError("not_found", "Project has no active release")
        content = ProjectContent.model_validate_json(record.release.content_json)
        workflow = next(
            (item for item in content.workflows if item.workflow_id == workflow_id), None
        )
        if workflow is None:
            raise ActivationError("not_found", "Workflow is not in the active release")
        return ReleaseBinding(
            scope.organization_id,
            scope.environment_id,
            record.release.release_id,
            record.release.executable_digest,
            record.request.configuration_revision,
            record.request.runtime_version,
        ), workflow.model_dump_json()
