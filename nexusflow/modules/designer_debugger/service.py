"""Authorize, freeze and dispatch Dev snapshots without changing activation pointers."""

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope, WorkflowDefinition
from nexusflow.modules.activity_catalog.catalog_types import CatalogEntry, CatalogRepository
from nexusflow.modules.activity_catalog.digests import bundle_digest
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.modules.designer_debugger.inspection import MAX_RESPONSE_BYTES, inspect_values
from nexusflow.modules.designer_debugger.inspection import inspect_inputs as inspect_debug_inputs
from nexusflow.modules.designer_debugger.inspection_types import DebugInputInspection
from nexusflow.modules.designer_debugger.listing_types import DebugSessionPage
from nexusflow.modules.designer_debugger.output_types import DebugOutputInspection
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
    DebugStepOutput,
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
        catalog: CatalogRepository | None = None,
    ) -> None:
        self.repository, self.projects, self.authorization = repository, projects, authorization
        self.runtime, self.placement, self.readiness = runtime, placement, readiness
        self.catalog = catalog

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

    def list_sessions(
        self,
        scope: Scope,
        project_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> DebugSessionPage:
        """Discover only this freshly authorized owner's saved session metadata."""
        actor = self._authorize(scope, project_id)
        try:
            return self.repository.list_sessions(scope, project_id, actor, limit, cursor)
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
        ):
            raise DebugError("unavailable", "Debug session history is unavailable") from None

    def _bound(self, record: DebugRecord, actor: str) -> None:
        if record.session.actor_id != actor:
            raise DebugError("forbidden", "Debug session belongs to another actor")
        if record.placement != self.placement:
            raise DebugError("conflict", "Debug session placement has changed")

    async def start(self, scope: Scope, project_id: str, request: DebugStart) -> DebugSession:
        return await self._start(scope, project_id, request)

    async def rerun(
        self, scope: Scope, project_id: str, session_id: str, request: DebugStart
    ) -> DebugSession:
        """Start an explicit fresh snapshot while keeping the selected session unchanged."""
        try:
            parent = self._record(scope, project_id, session_id)
            if request.operation_id == parent.request.operation_id:
                raise DebugError("conflict", "A rerun requires a new operation identifier")
            if request.workflow_id != parent.session.workflow_id:
                raise DebugError("conflict", "A rerun must select the original workflow")
            return await self._start(scope, project_id, request, parent.session.session_id)
        except PermissionError:
            # Keep an authorization port's explicit denial distinct from a
            # storage/provider outage, as on the normal start path.
            raise
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
        ):
            raise DebugError("unavailable", "Debug rerun provider is unavailable") from None

    async def _start(
        self,
        scope: Scope,
        project_id: str,
        request: DebugStart,
        parent_session_id: str | None = None,
    ) -> DebugSession:
        actor = self._authorize(scope, project_id)
        if self.placement.scope != scope:
            raise DebugError("forbidden", "Debug placement does not match scope")
        try:
            record = self.repository.operation(scope, project_id, request.operation_id)
            if record is None:
                record = self._prepare(scope, project_id, request, actor)
                record = self.repository.prepare(record, parent_session_id)
            self._bound(record, actor)
            self.repository.assert_origin(record, parent_session_id)
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

    def _source_workflow(
        self, scope: Scope, project_id: str, record: DebugRecord
    ) -> WorkflowDefinition:
        revision = self.projects.revision(scope, project_id, record.session.source_revision)
        if (revision.scope, revision.project_id, revision.number) != (
            scope,
            project_id,
            record.session.source_revision,
        ) or len(revision.content_json.encode()) > 4_000_000:
            raise DebugError("unavailable", "Debug source binding is unavailable")
        content = ProjectContent.model_validate_json(revision.content_json)
        workflow = next(
            (item for item in content.workflows if item.workflow_id == record.session.workflow_id),
            None,
        )
        if workflow is None:
            raise DebugError("unavailable", "Debug source binding is unavailable")
        encoded = json.dumps(
            workflow.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        checksum = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
        if checksum != record.plan.definition_digest:
            raise DebugError("unavailable", "Debug source binding is unavailable")
        return workflow

    async def inspect_inputs(
        self, scope: Scope, project_id: str, session_id: str
    ) -> DebugInputInspection:
        """Inspect frozen inputs under the exact original schema without touching runtime."""
        try:
            record = self._record(scope, project_id, session_id)
            workflow = self._source_workflow(scope, project_id, record)
            return inspect_debug_inputs(record, workflow.input_schema)
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
            VersioningError,
        ):
            raise DebugError("unavailable", "Debug inspection is unavailable") from None

    @staticmethod
    def _output_object(encoded: str) -> dict[str, JsonValue]:
        def unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
            value: dict[str, JsonValue] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate output key")
                value[key] = item
            return value

        def finite(value: str) -> float:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("Non-finite output number")
            return number

        def reject_constant(value: str) -> None:
            raise ValueError("Invalid output constant")

        result = json.loads(
            encoded, object_pairs_hook=unique, parse_float=finite, parse_constant=reject_constant
        )
        if type(result) is not dict:
            raise ValueError("Output root must be an object")
        return result

    async def inspect_output(
        self, scope: Scope, project_id: str, session_id: str, node_id: str
    ) -> DebugOutputInspection:
        """Project one completed output using its digest-bound activity schema."""
        try:
            record = self._record(scope, project_id, session_id)
            try:
                TypeAdapter(Identifier).validate_python(node_id, strict=True)
            except ValidationError:
                raise DebugError("invalid_request", "Invalid debug node identifier") from None
            workflow = self._source_workflow(scope, project_id, record)
            node = next((item for item in workflow.nodes if item.node_id == node_id), None)
            if node is None:
                raise DebugError("not_found", "Debug node was not found")
            if node.kind != "activity" or node.activity_ref is None:
                raise DebugError("conflict", "Debug node has no activity output")
            reference = node.activity_ref
            step = next((item for item in record.plan.steps if item.node_id == node_id), None)
            if step is None or (
                step.kind,
                step.activity_id,
                step.activity_version,
                step.artifact_digest,
            ) != ("activity", reference.activity_id, reference.version, reference.artifact_digest):
                raise DebugError("unavailable", "Debug activity binding is unavailable")
            if self.catalog is None:
                raise DebugError("unavailable", "Debug output schema is unavailable")
            entry = self.catalog.get(scope, reference)
            if (
                not isinstance(entry, CatalogEntry)
                or entry.scope != scope
                or entry.reference != reference
                or type(entry.manifest_json) is not str
                or len(entry.manifest_json.encode("utf-8")) > 262144
            ):
                raise DebugError("unavailable", "Debug output schema binding is unavailable")
            manifest = ActivityManifest.model_validate_json(entry.manifest_json)
            if (
                manifest.descriptor.activity_ref != reference
                or bundle_digest(manifest) != reference.artifact_digest
            ):
                raise DebugError("unavailable", "Debug output schema binding is unavailable")
            if not callable(getattr(self.runtime, "step_output", None)):
                raise DebugError("unavailable", "Debug output runtime is unavailable")
            assert record.session.temporal_run_id is not None
            output = await self.runtime.step_output(
                record.placement,
                record.session.execution_id,
                record.session.temporal_run_id,
                node_id,
            )
            if (
                not isinstance(output, DebugStepOutput)
                or not isinstance(output.snapshot, Snapshot)
                or output.node_id != node_id
                or node_id not in output.snapshot.completed_nodes
                or type(output.output_json) is not str
                or len(output.output_json.encode("utf-8")) > 65536
            ):
                raise DebugError("unavailable", "Debug output binding is unavailable")
            self._snapshot(record, output.snapshot)
            projected = inspect_values(
                record, self._output_object(output.output_json), manifest.descriptor.output_schema
            )
            result = DebugOutputInspection(
                session_id=record.session.session_id,
                workflow_id=record.session.workflow_id,
                source_revision=record.session.source_revision,
                definition_digest=record.session.definition_digest,
                node_id=node_id,
                activity_ref=reference,
                values=projected.values,
                redacted_paths=projected.redacted_paths,
                truncated=projected.truncated,
            )
            if len(result.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
                result = result.model_copy(
                    update={"values": {}, "redacted_paths": ("",), "truncated": True}
                )
            return result
        except PermissionError:
            raise
        except (
            OSError,
            RuntimeError,
            sqlite3.Error,
            ValidationError,
            ValueError,
            TypeError,
            RecursionError,
            OverflowError,
            VersioningError,
            CatalogError,
        ):
            raise DebugError("unavailable", "Debug output inspection is unavailable") from None
