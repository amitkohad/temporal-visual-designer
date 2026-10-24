"""Explicit application composition; required providers have no fake defaults."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from temporalio.client import Client

from nexusflow.apps.catalog_adapters import ScopedCatalog
from nexusflow.contracts.models import Scope
from nexusflow.modules.activity_catalog.catalog import CatalogService
from nexusflow.modules.activity_catalog.catalog_types import CatalogRepository, RuntimeCapabilities
from nexusflow.modules.activity_catalog.types import IntakeRepository
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.durable_runtime.starts import AsyncStartDispatcher, SQLiteStartRepository
from nexusflow.modules.durable_runtime.temporal_starts import (
    SQLiteDispatchJournal,
    TemporalStartDispatcher,
)
from nexusflow.modules.durable_runtime.types import ExecutionPlan, Snapshot
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow
from nexusflow.modules.human_tasks.form_types import TaskFieldPolicy
from nexusflow.modules.human_tasks.forms import TaskFormService
from nexusflow.modules.human_tasks.service import TaskService
from nexusflow.modules.human_tasks.types import AssignmentResolver, TaskAuthorization, TaskError
from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import Environment
from nexusflow.modules.package_promotion.readiness import ProofActivationReadiness
from nexusflow.modules.package_promotion.repository import SQLiteActivationRepository
from nexusflow.modules.package_promotion.service import ActivationService
from nexusflow.modules.package_promotion.types import ConfigurationCompatibility
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ProjectRepository


class RuntimeGateway(Protocol):
    async def dispatcher(self, placement: Environment) -> AsyncStartDispatcher: ...
    async def snapshot(self, placement: Environment, execution_id: str) -> Snapshot: ...


class PlacedStartDispatcher:
    """Resolve the remote client only when an intent still needs dispatch."""

    def __init__(self, gateway: RuntimeGateway, placement: Environment) -> None:
        self.gateway = gateway
        self.placement = placement

    async def start(self, execution_id: str, plan: ExecutionPlan) -> None:
        dispatcher = await self.gateway.dispatcher(self.placement)
        await dispatcher.start(execution_id, plan)


class TemporalRuntimeGateway:
    def __init__(
        self,
        clients: Callable[[Environment], Awaitable[Client]],
        journal: SQLiteDispatchJournal,
    ) -> None:
        self.clients = clients
        self.journal = journal

    async def _client(self, placement: Environment) -> Client:
        client = await self.clients(placement)
        if client.namespace != placement.temporal_namespace:
            raise OSError("Runtime namespace does not match placement")
        return client

    async def dispatcher(self, placement: Environment) -> AsyncStartDispatcher:
        return TemporalStartDispatcher(
            await self._client(placement), self.journal, placement.scope, placement.task_queue
        )

    async def snapshot(self, placement: Environment, execution_id: str) -> Snapshot:
        client = await self._client(placement)
        return await client.get_workflow_handle(execution_id).query(
            ProofWorkflow.snapshot, rpc_timeout=timedelta(seconds=10)
        )


class RequestAuthorization:
    def __init__(self, identity: IdentityService, token: str, request_id: str) -> None:
        self.identity = identity
        self.token = token
        self.request_id = request_id

    def authorize(self, scope: Scope, permission: str, project_id: str = "") -> str:
        context = self.identity.authorize(
            self.token,
            scope,
            permission,
            request_id=self.request_id,
            correlation_id=self.request_id,
        )
        return context.actor.subject


class RequestTaskAuthorization:
    """Authorize the same verified principal whose immutable oid owns the task."""

    def __init__(self, authorization: RequestAuthorization) -> None:
        self.authorization = authorization

    def authorize(self, scope: Scope, permission: str, resource_id: str) -> str:
        request = self.authorization
        return request.identity.authorize_user_object_id(
            request.token,
            scope,
            permission,
            request_id=request.request_id,
            correlation_id=request.request_id,
        )


@dataclass(frozen=True)
class ApiBackend:
    identity: IdentityService
    projects: ProjectRepository
    catalog: CatalogRepository
    intake: IntakeRepository
    activations: SQLiteActivationRepository
    starts: SQLiteStartRepository
    capabilities: RuntimeCapabilities
    configurations: ConfigurationCompatibility
    runtime: RuntimeGateway
    tasks_database: Path | None = None
    task_assignments: AssignmentResolver | None = None
    task_fields: TaskFieldPolicy | None = None

    @contextmanager
    def task_form_service(self, authorization: TaskAuthorization) -> Iterator[TaskFormService]:
        if self.tasks_database is None:
            raise TaskError("unavailable", "Human task provider is not configured")
        service = TaskFormService(self.tasks_database, authorization, self.task_fields)
        try:
            yield service
        finally:
            service.close()

    @contextmanager
    def task_service(self, authorization: TaskAuthorization) -> Iterator[TaskService]:
        if self.tasks_database is None:
            raise TaskError("unavailable", "Human task provider is not configured")
        service = TaskService(self.tasks_database, authorization, self.task_assignments)
        try:
            yield service
        finally:
            service.close()

    def catalog_service(self, authorization: RequestAuthorization) -> CatalogService:
        return CatalogService(self.catalog, self.intake, authorization, self.capabilities)

    def project_service(self, authorization: RequestAuthorization, scope: Scope) -> ProjectService:
        catalog = ScopedCatalog(self.catalog_service(authorization), scope)
        return ProjectService(self.projects, authorization, AuthoringValidator(catalog), catalog)

    def activation_service(self, authorization: RequestAuthorization) -> ActivationService:
        return ActivationService(
            self.activations,
            self.projects,
            authorization,
            ProofActivationReadiness(self.catalog, self.capabilities, self.configurations),
        )
