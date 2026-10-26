"""Versioned HTTP API factory; provider lifecycle is owned by the host application."""

import json
import sqlite3
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError

from nexusflow.apps.api_backend import (
    ApiBackend,
    PlacedStartDispatcher,
    RequestAuthorization,
    RequestTaskAuthorization,
)
from nexusflow.apps.api_boundary import ApiBoundary, error_response
from nexusflow.apps.api_models import (
    ActivityImport,
    CreateProject,
    CreateTemplateProject,
    ExecutionAccepted,
    ProjectActivation,
    ProjectChange,
    ProjectComparison,
    ProjectHistory,
    ProjectRelease,
    ProjectRevision,
    ProjectRevisionMetadata,
    PublishActivity,
    PublishProject,
    ReassignTask,
    RestoreProject,
    SaveProject,
    StartWorkflow,
    SubmitTaskForm,
    TaskFormSubmission,
    TransitionActivity,
    ValidateProject,
)
from nexusflow.contracts.models import ActivityRef, Digest, Identifier, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.activity_catalog.catalog_types import CatalogEntry
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError, ImportedBundle
from nexusflow.modules.designer_debugger.types import (
    DebugControl,
    DebugError,
    DebugSession,
    DebugStart,
    DebugStatus,
)
from nexusflow.modules.designer_forms.form_types import FormError, FormView
from nexusflow.modules.designer_forms.templates import (
    TemplateInfo,
    build_template,
    get_template,
    list_templates,
)
from nexusflow.modules.durable_runtime.starts import AsyncStartService, StartError, StartRequest
from nexusflow.modules.durable_runtime.types import CommandReceipt, Snapshot
from nexusflow.modules.human_tasks.types import (
    DecisionRequest,
    DelegationRequest,
    ReassignmentRequest,
    TaskError,
    TaskEvent,
    TaskPage,
    TaskStatus,
    TaskView,
)
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.package_promotion.attachments import AttachmentMetadata
from nexusflow.modules.package_promotion.configuration_types import (
    ConfigurationMetadata,
    ConfigurationPage,
    ConfigurationWrite,
)
from nexusflow.modules.package_promotion.materialization_types import (
    MaterializationReceipt,
    MaterializationRequest,
)
from nexusflow.modules.package_promotion.package_format import PackageManifest
from nexusflow.modules.package_promotion.package_imports import (
    PackageImportMetadata,
    PackageImportPage,
)
from nexusflow.modules.package_promotion.service import ActivatedReleaseResolver
from nexusflow.modules.package_promotion.types import (
    ActivationError,
    ActivationHistory,
    ActivationRecord,
    ActivationRequest,
    ActivationSummary,
)
from nexusflow.modules.project_versioning.types import ProjectRecord, Revision, VersioningError


@dataclass(frozen=True)
class Session:
    scope: Scope
    authorization: RequestAuthorization


class RequestStartAuthorization:
    def __init__(self, authorization: RequestAuthorization) -> None:
        self.authorization = authorization

    def authorize(self, scope: Scope, project_id: str) -> None:
        self.authorization.authorize(scope, "run.start", project_id)


def revision_view(revision: Revision) -> ProjectRevision:
    return ProjectRevision.model_validate(
        {
            "project_id": revision.project_id,
            "revision": revision.number,
            "parent_revision": revision.parent_revision,
            "summary": revision.summary,
            "author_id": revision.author_id,
            "created_at": revision.created_at,
            "content": json.loads(revision.content_json),
        }
    )


def import_view(imported: ImportedBundle) -> ActivityImport:
    return ActivityImport(
        digest=imported.digest,
        manifest=ActivityManifest.model_validate_json(imported.manifest_json),
        state=imported.state,
        author_id=imported.author_id,
        created_at=imported.created_at,
    )


def activation_view(record: ActivationRecord | None) -> ProjectActivation:
    if record is None:
        return ProjectActivation(revision=0, release_id=None)
    return ProjectActivation.model_validate(
        {
            "project_id": record.request.project_id,
            "operation_id": record.request.operation_id,
            "release_id": record.release.release_id,
            "revision": record.revision,
            "previous_release_id": record.previous_release_id,
            "runtime_version": record.request.runtime_version,
            "configuration_revision": record.request.configuration_revision,
            "checksum": record.release.executable_digest,
            "actor_id": record.actor_id,
            "created_at": record.created_at,
            "evidence": record.request.evidence,
        }
    )


def create_app(backend: ApiBackend, *, maximum_body_bytes: int = 6_000_000) -> FastAPI:
    app = FastAPI(
        title="Nexus Flow Designer API",
        version="0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(ApiBoundary, maximum_body_bytes=maximum_body_bytes)

    async def domain_error(request: Request, error: Exception) -> JSONResponse:
        code = getattr(error, "code", "unavailable")
        status = {
            "unauthenticated": 401,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "referenced": 409,
            "dependency_conflict": 409,
            "unavailable": 503,
            "assignment_unavailable": 503,
            "delivery_unavailable": 503,
            "attachment_too_large": 413,
            "attachment_limit": 409,
            "import_limit": 409,
        }.get(code, 422)
        normalized = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            503: "unavailable",
        }.get(status, "invalid_input")
        message = {
            401: "Invalid access token",
            403: "Access denied",
            404: "Resource was not found",
            409: "Request conflicts with current state",
            503: "A required service is unavailable",
        }.get(status, "Request could not be validated")
        return error_response(request.state.request_id, status, normalized, message)

    for error_type in (
        IdentityError,
        CatalogError,
        VersioningError,
        ActivationError,
        StartError,
        TaskError,
        FormError,
        DebugError,
        RPCError,
        OSError,
        sqlite3.Error,
        ApplicationError,
    ):
        app.add_exception_handler(error_type, domain_error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(
            request.state.request_id, 422, "invalid_input", "Invalid request fields or JSON"
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(
            request.state.request_id,
            error.status_code,
            "not_found" if error.status_code == 404 else "invalid_input",
            "Route was not found" if error.status_code == 404 else "Request is unsupported",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return error_response(
            getattr(request.state, "request_id", "unknown"),
            500,
            "unavailable",
            "The request could not be completed",
        )

    def session(
        request: Request,
        organization_id: Identifier,
        environment_id: Literal["dev", "uat", "prod"],
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
        ],
    ) -> Session:
        if credentials is None or not credentials.credentials.strip():
            raise IdentityError("unauthenticated", "Bearer token required")
        scope = Scope(organization_id=organization_id, environment_id=environment_id)
        authorization = RequestAuthorization(
            backend.identity, credentials.credentials, request.state.request_id
        )
        authorization.authorize(scope, "environment.read")
        return Session(scope, authorization)

    SessionDependency = Annotated[Session, Depends(session)]
    prefix = "/api/v1/organizations/{organization_id}/environments/{environment_id}"

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1"}

    @app.get(prefix + "/schema")
    def schema(context: SessionDependency) -> dict[str, object]:
        return app.openapi()

    @app.get(prefix + "/context")
    def context(context: SessionDependency) -> dict[str, str]:
        return context.scope.model_dump()

    @app.get(prefix + "/tasks")
    def tasks(
        context: SessionDependency,
        status: TaskStatus | None = None,
        run_id: Identifier | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> TaskPage:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.list_tasks(
                context.scope, status=status, run_id=run_id, limit=limit, offset=offset
            )

    @app.get(prefix + "/tasks/{task_id}")
    def task(task_id: Identifier, context: SessionDependency) -> TaskView:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.get(context.scope, task_id)

    @app.get(prefix + "/tasks/{task_id}/form")
    def task_form(task_id: Identifier, context: SessionDependency) -> FormView:
        with backend.task_form_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.preview(context.scope, task_id)

    @app.post(prefix + "/tasks/{task_id}/form")
    def submit_task_form(
        task_id: Identifier, payload: SubmitTaskForm, context: SessionDependency
    ) -> TaskFormSubmission:
        with backend.task_form_service(RequestTaskAuthorization(context.authorization)) as service:
            revision = service.submit(
                context.scope,
                task_id,
                payload.expected_revision,
                json.dumps(payload.values, allow_nan=False),
            )
            return TaskFormSubmission(revision=revision)

    @app.get(prefix + "/tasks/{task_id}/audit")
    def task_audit(task_id: Identifier, context: SessionDependency) -> tuple[TaskEvent, ...]:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.audit(context.scope, task_id)

    @app.post(prefix + "/tasks/{task_id}/decisions")
    def decide_task(
        task_id: Identifier, payload: DecisionRequest, context: SessionDependency
    ) -> TaskView:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.decide(context.scope, task_id, payload)

    @app.post(prefix + "/tasks/{task_id}/reassignments")
    def reassign_task(
        task_id: Identifier, payload: ReassignTask, context: SessionDependency
    ) -> TaskView:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            command = ReassignmentRequest(
                command_id=payload.command_id,
                expected_revision=payload.expected_revision,
                user_ids=tuple(payload.user_ids),
                group_ids=tuple(payload.group_ids),
            )
            return service.reassign(context.scope, task_id, command)

    @app.post(prefix + "/tasks/{task_id}/delegations")
    def delegate_task(
        task_id: Identifier, payload: DelegationRequest, context: SessionDependency
    ) -> TaskView:
        with backend.task_service(RequestTaskAuthorization(context.authorization)) as service:
            return service.delegate(context.scope, task_id, payload)

    @app.get(prefix + "/project-templates")
    def project_templates(context: SessionDependency) -> tuple[TemplateInfo, ...]:
        context.authorization.authorize(context.scope, "project.read")
        return list_templates()

    @app.get(prefix + "/project-templates/{template_id}")
    def project_template(template_id: Identifier, context: SessionDependency) -> TemplateInfo:
        context.authorization.authorize(context.scope, "project.read")
        return get_template(template_id)

    @app.post(prefix + "/project-templates/{template_id}/projects", status_code=201)
    def create_template_project(
        template_id: Identifier, payload: CreateTemplateProject, context: SessionDependency
    ) -> ProjectRevision:
        context.authorization.authorize(context.scope, "project.write", payload.project_id)
        if context.scope.environment_id != "dev":
            raise VersioningError("forbidden", "Project authoring requires Dev")
        content = build_template(
            template_id,
            {slot: value.to_assignment() for slot, value in payload.assignments.items()},
        )
        return revision_view(
            backend.project_service(context.authorization, context.scope).create(
                context.scope, payload.project_id, payload.name, content.model_dump_json()
            )
        )

    @app.get(prefix + "/projects")
    def projects(
        context: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> tuple[ProjectRecord, ...]:
        return backend.project_service(context.authorization, context.scope).list_projects(
            context.scope, limit, offset
        )

    @app.post(prefix + "/projects", status_code=201)
    def create_project(payload: CreateProject, context: SessionDependency) -> ProjectRevision:
        result = backend.project_service(context.authorization, context.scope).create(
            context.scope,
            payload.project_id,
            payload.name,
            json.dumps(payload.content, allow_nan=False),
        )
        return revision_view(result)

    @app.get(prefix + "/projects/{project_id}")
    def read_project(
        project_id: Identifier,
        context: SessionDependency,
        revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> ProjectRevision:
        return revision_view(
            backend.project_service(context.authorization, context.scope).read(
                context.scope, project_id, revision
            )
        )

    @app.put(
        prefix + "/projects/{project_id}/configurations/{configuration_revision}", status_code=201
    )
    def put_configuration(
        project_id: Identifier,
        configuration_revision: Identifier,
        payload: ConfigurationWrite,
        context: SessionDependency,
    ) -> ConfigurationMetadata:
        return backend.configuration_service(context.authorization, context.scope).put(
            context.scope, project_id, configuration_revision, payload
        )

    @app.get(prefix + "/projects/{project_id}/configurations/{configuration_revision}")
    def get_configuration(
        project_id: Identifier,
        configuration_revision: Identifier,
        context: SessionDependency,
    ) -> ConfigurationMetadata:
        return backend.configuration_service(context.authorization, context.scope).get(
            context.scope, project_id, configuration_revision
        )

    @app.get(prefix + "/projects/{project_id}/configurations")
    def list_configurations(
        project_id: Identifier,
        context: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before_revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> ConfigurationPage:
        return backend.configuration_service(context.authorization, context.scope).list(
            context.scope, project_id, limit=limit, before_revision=before_revision
        )

    @app.get(prefix + "/projects/{project_id}/history")
    def project_history(
        project_id: Identifier,
        context: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before_revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> ProjectHistory:
        page = backend.project_service(context.authorization, context.scope).history_page(
            context.scope, project_id, limit=limit, before_revision=before_revision
        )
        return ProjectHistory(
            project_id=page.project_id,
            revisions=tuple(
                ProjectRevisionMetadata(
                    revision=item.number,
                    parent_revision=item.parent_revision,
                    summary=item.summary,
                    author_id=item.author_id,
                    created_at=item.created_at,
                )
                for item in page.revisions
            ),
            next_before_revision=page.next_before_revision,
        )

    @app.get(prefix + "/projects/{project_id}/compare")
    def compare_project(
        project_id: Identifier,
        context: SessionDependency,
        before_revision: Annotated[int, Query(ge=1, le=2**63 - 1)],
        after_revision: Annotated[int, Query(ge=1, le=2**63 - 1)],
        include_layout: bool = False,
    ) -> ProjectComparison:
        changes = backend.project_service(context.authorization, context.scope).compare_bounded(
            context.scope,
            project_id,
            before_revision,
            after_revision,
            include_layout=include_layout,
        )
        return ProjectComparison(
            project_id=project_id,
            before_revision=before_revision,
            after_revision=after_revision,
            include_layout=include_layout,
            changes=tuple(
                ProjectChange(
                    path=item.path, before_json=item.before_json, after_json=item.after_json
                )
                for item in changes
            ),
        )

    @app.post(prefix + "/projects/{project_id}/restore", status_code=201)
    def restore_project(
        project_id: Identifier, payload: RestoreProject, context: SessionDependency
    ) -> ProjectRevision:
        return revision_view(
            backend.project_service(context.authorization, context.scope).restore(
                context.scope,
                project_id,
                payload.source_revision,
                payload.expected_revision,
                payload.summary,
            )
        )

    @app.put(prefix + "/projects/{project_id}")
    def save_project(
        project_id: Identifier, payload: SaveProject, context: SessionDependency
    ) -> ProjectRevision:
        return revision_view(
            backend.project_service(context.authorization, context.scope).save(
                context.scope,
                project_id,
                payload.expected_revision,
                json.dumps(payload.content, allow_nan=False),
                payload.summary,
            )
        )

    @app.post(prefix + "/projects/{project_id}/validation")
    def validate_project(
        project_id: Identifier, payload: ValidateProject, context: SessionDependency
    ) -> tuple[ValidationIssue, ...]:
        return backend.project_service(context.authorization, context.scope).validate(
            context.scope, project_id, json.dumps(payload.content, allow_nan=False)
        )

    @app.post(prefix + "/projects/{project_id}/releases", status_code=201)
    def publish(
        project_id: Identifier, payload: PublishProject, context: SessionDependency
    ) -> ProjectRelease:
        release = backend.project_service(context.authorization, context.scope).publish(
            context.scope, project_id, payload.source_revision, payload.idempotency_key
        )
        return ProjectRelease.model_validate(
            {
                "project_id": project_id,
                "release_id": release.release_id,
                "source_revision": release.source_revision,
                "checksum": release.executable_digest,
            }
        )

    @app.get(
        prefix + "/projects/{project_id}/releases/{release_id}/package",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def export_project_package(
        project_id: Identifier, release_id: Identifier, context: SessionDependency
    ) -> Response:
        package = backend.project_package_service(context.authorization).export(
            context.scope, project_id, release_id
        )
        return Response(
            package.archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="nexus-flow-project.zip"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "X-Package-Digest": package.manifest.package_digest,
            },
        )

    @app.put(
        prefix + "/package-imports/{operation_id}",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def stage_package_import(
        operation_id: Identifier, request: Request, context: SessionDependency
    ) -> PackageImportMetadata:
        service = await run_in_threadpool(
            backend.package_import_service, context.authorization, context.scope
        )
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/zip":
            raise HTTPException(415)
        archive = await request.body()
        return await run_in_threadpool(service.stage, context.scope, operation_id, archive)

    @app.post(prefix + "/package-imports/{import_operation_id}/materializations", status_code=201)
    def materialize_package_import(
        import_operation_id: Identifier,
        request: MaterializationRequest,
        context: SessionDependency,
    ) -> MaterializationReceipt:
        return backend.materialization_service(context.authorization, context.scope).materialize(
            context.scope, import_operation_id, request
        )

    @app.get(prefix + "/package-imports/{import_operation_id}/materializations/{operation_id}")
    def get_package_materialization(
        import_operation_id: Identifier, operation_id: Identifier, context: SessionDependency
    ) -> MaterializationReceipt:
        return backend.materialization_service(context.authorization, context.scope).receipt(
            context.scope, import_operation_id, operation_id
        )

    @app.get(prefix + "/package-imports")
    def list_package_imports(
        context: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before_revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> PackageImportPage:
        return backend.package_import_service(context.authorization, context.scope).list(
            context.scope, limit=limit, before_revision=before_revision
        )

    @app.get(prefix + "/package-imports/{operation_id}")
    def get_package_import(
        operation_id: Identifier, context: SessionDependency
    ) -> PackageImportMetadata:
        return backend.package_import_service(context.authorization, context.scope).get(
            context.scope, operation_id
        )

    @app.get(prefix + "/package-imports/{operation_id}/manifest")
    def get_package_import_manifest(
        operation_id: Identifier, context: SessionDependency
    ) -> PackageManifest:
        return backend.package_import_service(context.authorization, context.scope).manifest(
            context.scope, operation_id
        )

    @app.get(
        prefix + "/package-imports/{operation_id}/archive",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def download_package_import(operation_id: Identifier, context: SessionDependency) -> Response:
        result = backend.package_import_service(context.authorization, context.scope).download(
            context.scope, operation_id
        )
        return Response(
            result.archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="nexus-flow-project.zip"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "X-Package-Digest": result.metadata.package_digest,
            },
        )

    @app.post(
        prefix + "/packages/validation",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def validate_project_package(
        request: Request, context: SessionDependency
    ) -> PackageManifest:
        await run_in_threadpool(
            context.authorization.authorize, context.scope, "package.import", "packages"
        )
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/zip":
            raise HTTPException(415)
        archive = await request.body()
        return await run_in_threadpool(
            backend.project_package_service(context.authorization).validate, context.scope, archive
        )

    @app.post(
        prefix + "/activity-imports",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def import_activity(request: Request, context: SessionDependency) -> ActivityImport:
        # Authorization precedes media validation and reading the boundary-buffered archive.
        await run_in_threadpool(context.authorization.authorize, context.scope, "activity.import")
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/zip":
            raise HTTPException(415)
        archive = await request.body()
        imported = await run_in_threadpool(
            backend.intake_service(context.authorization).import_bundle, context.scope, archive
        )
        return import_view(imported)

    @app.get(prefix + "/activity-imports/{digest}")
    def read_activity_import(digest: Digest, context: SessionDependency) -> ActivityImport:
        return import_view(
            backend.intake_service(context.authorization).read_import(context.scope, digest)
        )

    @app.get(
        prefix + "/activity-imports/{digest}/archive",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            }
        },
    )
    def download_activity_import(digest: Digest, context: SessionDependency) -> Response:
        archive = backend.intake_service(context.authorization).download_import(
            context.scope, digest
        )
        return Response(
            archive,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="activity-bundle.zip"'},
        )

    @app.post(prefix + "/activities/publish", status_code=201)
    def publish_activity(payload: PublishActivity, context: SessionDependency) -> CatalogEntry:
        return backend.catalog_service(context.authorization).publish(
            context.scope, payload.artifact_digest
        )

    @app.post(prefix + "/activities/resolve")
    def read_activity(payload: ActivityRef, context: SessionDependency) -> CatalogEntry:
        return backend.catalog_service(context.authorization).get(context.scope, payload)

    @app.post(prefix + "/activities/transitions")
    def transition_activity(
        payload: TransitionActivity, context: SessionDependency
    ) -> CatalogEntry:
        return backend.catalog_service(context.authorization).transition(
            context.scope, payload.reference, payload.target, payload.expected_generation
        )

    @app.get(prefix + "/activities")
    def activities(
        context: SessionDependency,
        query: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ) -> tuple[CatalogEntry, ...]:
        return backend.catalog_service(context.authorization).search(
            context.scope, query=query, limit=limit, offset=offset
        )

    @app.get(prefix + "/projects/{project_id}/activations")
    def activation_history(
        project_id: Identifier,
        context: SessionDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        before_revision: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> ActivationHistory:
        return backend.activation_service(context.authorization).history_page(
            context.scope, project_id, limit=limit, before_revision=before_revision
        )

    @app.get(prefix + "/projects/{project_id}/activations/{operation_id}")
    def activation_operation(
        project_id: Identifier, operation_id: Identifier, context: SessionDependency
    ) -> ActivationSummary:
        return backend.activation_service(context.authorization).operation(
            context.scope, project_id, operation_id
        )

    @app.put(
        prefix + "/projects/{project_id}/activations/{operation_id}/attachments/{attachment_id}",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    async def upload_deployment_attachment(
        request: Request,
        project_id: Identifier,
        operation_id: Identifier,
        attachment_id: Identifier,
        context: SessionDependency,
        display_name: Annotated[str, Query(min_length=1, max_length=128)] = "Approval evidence",
    ) -> AttachmentMetadata:
        await run_in_threadpool(
            context.authorization.authorize, context.scope, "package.activate", project_id
        )
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/octet-stream":
            raise HTTPException(415)
        content = await request.body()
        return await run_in_threadpool(
            backend.attachment_service(context.authorization).upload,
            context.scope,
            project_id,
            operation_id,
            attachment_id,
            content,
            display_name,
        )

    @app.get(prefix + "/projects/{project_id}/activations/{operation_id}/attachments")
    def deployment_attachments(
        project_id: Identifier, operation_id: Identifier, context: SessionDependency
    ) -> tuple[AttachmentMetadata, ...]:
        return backend.attachment_service(context.authorization).list(
            context.scope, project_id, operation_id
        )

    @app.get(
        prefix + "/projects/{project_id}/activations/{operation_id}/attachments/{attachment_id}",
        response_class=Response,
        responses={
            200: {
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                }
            }
        },
    )
    def download_deployment_attachment(
        project_id: Identifier,
        operation_id: Identifier,
        attachment_id: Identifier,
        context: SessionDependency,
    ) -> Response:
        download = backend.attachment_service(context.authorization).download(
            context.scope, project_id, operation_id, attachment_id
        )
        return Response(
            download.content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="deployment-evidence.bin"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(prefix + "/projects/{project_id}/activation")
    def current_activation(project_id: Identifier, context: SessionDependency) -> ProjectActivation:
        return activation_view(
            backend.activation_service(context.authorization).current(context.scope, project_id)
        )

    @app.post(prefix + "/projects/{project_id}/activation")
    def activate(
        project_id: Identifier, payload: ActivationRequest, context: SessionDependency
    ) -> ProjectActivation:
        if payload.project_id != project_id:
            raise VersioningError("invalid_request", "Project identity mismatch")
        return activation_view(
            backend.activation_service(context.authorization).apply(context.scope, payload)
        )

    @app.post(prefix + "/projects/{project_id}/debug-sessions", status_code=202)
    async def start_debug(
        project_id: Identifier, payload: DebugStart, context: SessionDependency
    ) -> DebugSession:
        return await backend.debug_service(context.authorization, context.scope).start(
            context.scope, project_id, payload
        )

    @app.get(prefix + "/projects/{project_id}/debug-sessions/{session_id}")
    async def debug_status(
        project_id: Identifier, session_id: Identifier, context: SessionDependency
    ) -> DebugStatus:
        return await backend.debug_service(context.authorization, context.scope).status(
            context.scope, project_id, session_id
        )

    @app.post(prefix + "/projects/{project_id}/debug-sessions/{session_id}/commands")
    async def debug_control(
        project_id: Identifier,
        session_id: Identifier,
        payload: DebugControl,
        context: SessionDependency,
    ) -> CommandReceipt:
        return await backend.debug_service(context.authorization, context.scope).control(
            context.scope, project_id, session_id, payload
        )

    @app.post(prefix + "/projects/{project_id}/executions", status_code=202)
    async def start(
        project_id: Identifier, payload: StartWorkflow, context: SessionDependency
    ) -> ExecutionAccepted:
        authorization = RequestStartAuthorization(context.authorization)
        authorization.authorize(context.scope, project_id)
        placement = backend.identity.placement(
            context.authorization.token,
            context.scope,
            request_id=context.authorization.request_id,
            correlation_id=context.authorization.request_id,
        )
        dispatcher = PlacedStartDispatcher(backend.runtime, placement)
        service = AsyncStartService(
            backend.starts, authorization, ActivatedReleaseResolver(backend.activations), dispatcher
        )
        receipt = await service.start(
            StartRequest(
                context.scope,
                project_id,
                payload.workflow_id,
                payload.idempotency_key,
                json.dumps(payload.input, allow_nan=False),
                payload.source,
            )
        )
        return ExecutionAccepted.model_validate(
            {
                "execution_id": receipt.execution_id,
                "release_id": receipt.plan.binding.release_id,
                "configuration_revision": receipt.plan.binding.configuration_revision,
                "state": "accepted",
            }
        )

    @app.get(prefix + "/projects/{project_id}/executions/{execution_id}")
    async def execution(
        project_id: Identifier, execution_id: Identifier, context: SessionDependency
    ) -> Snapshot:
        context.authorization.authorize(context.scope, "run.read", project_id)
        receipt = backend.starts.execution(context.scope, project_id, execution_id)
        if receipt is None:
            raise StartError("not_found", "Execution was not found")
        if not receipt.dispatched:
            return Snapshot(
                receipt.plan.binding.release_id,
                receipt.plan.definition_digest,
                receipt.plan.binding.runtime_version,
                "",
                "dispatch_pending",
                0,
                (),
            )
        placement = backend.identity.placement(
            context.authorization.token,
            context.scope,
            request_id=context.authorization.request_id,
            correlation_id=context.authorization.request_id,
        )
        snapshot = await backend.runtime.snapshot(placement, execution_id)
        if (snapshot.release_id, snapshot.definition_digest, snapshot.runtime_version) != (
            receipt.plan.binding.release_id,
            receipt.plan.definition_digest,
            receipt.plan.binding.runtime_version,
        ):
            raise StartError("conflict", "Runtime snapshot does not match the pinned execution")
        return snapshot

    return app
