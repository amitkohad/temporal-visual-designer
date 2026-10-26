"""Authorize, freeze and dispatch Dev snapshots without changing activation pointers."""

import hashlib
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.designer_debugger.repository import SQLiteDebugSessionRepository
from nexusflow.modules.designer_debugger.types import (
    DebugControl,
    DebugError,
    DebugRecord,
    DebugRuntime,
    DebugSession,
    DebugStart,
    DebugStatus,
)
from nexusflow.modules.durable_runtime.preflight import PlanError, compile_plan
from nexusflow.modules.durable_runtime.types import (
    CommandReceipt,
    DebugCommand,
    ReleaseBinding,
    Snapshot,
)
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.modules.package_promotion.service import validate_release
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationReadiness,
    ActivationRequest,
)
from nexusflow.modules.project_versioning.canonical import canonical, digest, executable
from nexusflow.modules.project_versioning.types import (
    ProjectAuthorization,
    ProjectContent,
    ProjectRepository,
    Release,
    VersioningError,
)


class DebugSessionService:
    def __init__(
        self,
        repository: SQLiteDebugSessionRepository,
        projects: ProjectRepository,
        authorization: ProjectAuthorization,
        runtime: DebugRuntime,
        placement: Environment,
        readiness: ActivationReadiness,
    ) -> None:
        self.repository, self.projects, self.authorization = repository, projects, authorization
        self.runtime, self.placement, self.readiness = runtime, placement, readiness

    def _authorize(self, scope: Scope, project_id: str, session_id: str | None = None) -> str:
        actor = self.authorization.authorize(scope, "debug.execute", project_id)
        if scope.environment_id != "dev":
            raise DebugError("forbidden", "Debug sessions are restricted to Dev")
        try:
            TypeAdapter(Identifier).validate_python(project_id, strict=True)
            if session_id is not None:
                TypeAdapter(Identifier).validate_python(session_id, strict=True)
        except ValidationError:
            raise DebugError("invalid_request", "Invalid debug identifier") from None
        return actor

    def _bound(self, record: DebugRecord, actor: str) -> None:
        if record.session.actor_id != actor:
            raise DebugError("forbidden", "Debug session belongs to another actor")
        if record.placement != self.placement:
            raise DebugError("conflict", "Debug session placement has changed")

    async def start(self, scope: Scope, project_id: str, request: DebugStart) -> DebugSession:
        actor = self._authorize(scope, project_id)
        if self.placement.scope != scope:
            raise DebugError("forbidden", "Debug placement does not match scope")
        try:
            record = self.repository.operation(scope, project_id, request.operation_id)
            if record is None:
                record = self._prepare(scope, project_id, request, actor)
                record = self.repository.prepare(record)
            self._bound(record, actor)
            if record.request != request:
                raise DebugError("conflict", "Debug operation already has another request")
            if record.session.state == "started":
                return record.session
            run_id = await self.runtime.start(
                record.placement, record.session.execution_id, record.plan
            )
            return self.repository.acknowledge(
                scope, project_id, record.session.session_id, run_id
            ).session
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
        ):
            raise DebugError("unavailable", "Debug provider is unavailable") from None
        except VersioningError as error:
            raise DebugError(error.code, "Debug source revision is unavailable") from None

    def _prepare(
        self, scope: Scope, project_id: str, request: DebugStart, actor: str
    ) -> DebugRecord:
        revision = self.projects.revision(scope, project_id, request.source_revision)
        if (revision.scope, revision.project_id, revision.number) != (
            scope,
            project_id,
            request.source_revision,
        ):
            raise DebugError("unavailable", "Source revision binding is invalid")
        if len(revision.content_json.encode()) > 4_000_000:
            raise DebugError("invalid_request", "Debug project exceeds limits")
        content = ProjectContent.model_validate_json(revision.content_json)
        references = {ref.model_dump_json(): ref for ref in content.activity_refs}
        references.update(
            {
                node.activity_ref.model_dump_json(): node.activity_ref
                for workflow in content.workflows
                for node in workflow.nodes
                if node.activity_ref is not None
            }
        )
        artifacts = [
            ref.model_dump(mode="json")
            for ref in sorted(
                references.values(),
                key=lambda ref: (ref.activity_id, ref.version, ref.artifact_digest),
            )
        ]
        session_id = (
            "debug-"
            + hashlib.sha256(
                canonical(
                    [scope.organization_id, scope.environment_id, project_id, request.operation_id]
                ).encode()
            ).hexdigest()
        )
        release = Release(
            scope,
            project_id,
            session_id,
            revision.number,
            canonical(content.model_dump(mode="json")),
            canonical(artifacts),
            digest({"content": content.model_dump(mode="json"), "artifacts": artifacts}),
            digest({"content": executable(content), "artifacts": artifacts}),
            actor,
            datetime.now(UTC).isoformat(),
        )
        activation = ActivationRequest(
            project_id=project_id,
            release_id=session_id,
            configuration_revision=request.configuration_revision,
            operation_id=request.operation_id,
            expected_revision=0,
        )
        try:
            validate_release(release, scope, activation)
            if self.readiness.validate(scope, release, activation):
                raise DebugError("incompatible_release", "Debug source or destination is not ready")
            workflow = next(
                (item for item in content.workflows if item.workflow_id == request.workflow_id),
                None,
            )
            if workflow is None:
                raise DebugError("not_found", "Debug workflow was not found")
            plan = compile_plan(
                workflow.model_dump_json(),
                ReleaseBinding(
                    scope.organization_id,
                    scope.environment_id,
                    session_id,
                    release.executable_digest,
                    request.configuration_revision,
                ),
                scope,
                inputs=request.input,
                debug=True,
                breakpoints=request.breakpoints,
            )
            self.readiness.retain(scope, release, session_id)
        except (ActivationError, PlanError) as error:
            raise DebugError("incompatible_release", "Debug source cannot execute") from error
        session = DebugSession(
            session_id=session_id,
            project_id=project_id,
            source_revision=revision.number,
            workflow_id=request.workflow_id,
            configuration_revision=request.configuration_revision,
            definition_digest=plan.definition_digest,
            actor_id=actor,
            created_at=release.created_at,
            execution_id="nf-" + uuid4().hex,
            temporal_run_id=None,
            state="dispatch_pending",
        )
        return DebugRecord(request, session, self.placement, plan)

    def _record(self, scope: Scope, project_id: str, session_id: str) -> DebugRecord:
        actor = self._authorize(scope, project_id, session_id)
        record = self.repository.get(scope, project_id, session_id)
        self._bound(record, actor)
        if record.session.state != "started" or record.session.temporal_run_id is None:
            raise DebugError("conflict", "Debug dispatch has not been acknowledged")
        return record

    @staticmethod
    def _snapshot(record: DebugRecord, snapshot: Snapshot) -> None:
        if (snapshot.release_id, snapshot.definition_digest, snapshot.runtime_version) != (
            record.plan.binding.release_id,
            record.plan.definition_digest,
            record.plan.binding.runtime_version,
        ):
            raise DebugError("unavailable", "Debug runtime binding does not match")

    async def status(self, scope: Scope, project_id: str, session_id: str) -> DebugStatus:
        try:
            record = self._record(scope, project_id, session_id)
            assert record.session.temporal_run_id is not None
            snapshot = await self.runtime.snapshot(
                record.placement, record.session.execution_id, record.session.temporal_run_id
            )
            self._snapshot(record, snapshot)
            return DebugStatus(session=record.session, snapshot=snapshot)
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
        ):
            raise DebugError("unavailable", "Debug provider is unavailable") from None

    async def control(
        self, scope: Scope, project_id: str, session_id: str, request: DebugControl
    ) -> CommandReceipt:
        try:
            record = self._record(scope, project_id, session_id)
            previous = self.repository.claim_command(scope, project_id, session_id, request)
            if previous is not None:
                return previous
            assert record.session.temporal_run_id is not None
            snapshot = await self.runtime.snapshot(
                record.placement, record.session.execution_id, record.session.temporal_run_id
            )
            self._snapshot(record, snapshot)
            receipt = await self.runtime.control(
                record.placement,
                record.session.execution_id,
                record.session.temporal_run_id,
                DebugCommand(
                    request.command_id, request.node_id, request.action, request.expected_revision
                ),
            )
            return self.repository.acknowledge_command(
                scope, project_id, session_id, request, receipt
            )
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
        ):
            raise DebugError("unavailable", "Debug provider is unavailable") from None
