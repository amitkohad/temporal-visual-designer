"""Authorized project history and whole-project release orchestration."""

from pydantic import TypeAdapter, ValidationError

from nexusflow.contracts.models import ActivityRef, Identifier, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.project_versioning.canonical import (
    canonical,
    compare_json,
    digest,
    executable,
)
from nexusflow.modules.project_versioning.types import (
    ArtifactResolver,
    Change,
    ContentValidator,
    HistoryPage,
    ProjectAuthorization,
    ProjectContent,
    ProjectRecord,
    ProjectRepository,
    Release,
    Revision,
    VersioningError,
)


class ProjectService:
    def validate(
        self, scope: Scope, project_id: str, content_json: str
    ) -> tuple[ValidationIssue, ...]:
        self._authorize(scope, project_id, True)
        return self.validator.validate(self._content(content_json))

    def list_projects(
        self, scope: Scope, limit: int = 50, offset: int = 0
    ) -> tuple[ProjectRecord, ...]:
        self.authorization.authorize(scope, "project.read", "projects")
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 10000
        ):
            raise VersioningError("invalid_request", "Invalid project pagination")
        return self.repository.list_projects(scope, limit, offset)

    def __init__(
        self,
        repository: ProjectRepository,
        authorization: ProjectAuthorization,
        validator: ContentValidator,
        artifacts: ArtifactResolver,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.validator = validator
        self.artifacts = artifacts

    def _authorize(self, scope: Scope, project_id: str, write: bool = False) -> str:
        actor = self.authorization.authorize(
            scope, "project.write" if write else "project.read", project_id
        )
        if write and scope.environment_id != "dev":
            raise VersioningError("forbidden", "Project authoring is restricted to Dev")
        try:
            TypeAdapter(Identifier).validate_python(project_id)
        except ValidationError:
            raise VersioningError("invalid_request", "Invalid project identifier") from None
        return actor

    @staticmethod
    def _content(payload: str) -> ProjectContent:
        try:
            size = len(payload.encode("utf-8"))
        except UnicodeError:
            raise VersioningError("invalid_content", "Project must contain valid Unicode") from None
        if size > 4_000_000:
            raise VersioningError("invalid_content", "Project snapshot exceeds size limit")
        try:
            content = ProjectContent.model_validate_json(payload)
            canonical(content.model_dump(mode="json"))
            return content
        except ValidationError as error:
            issues = tuple(
                ValidationIssue(
                    "/"
                    + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in item["loc"]),
                    "invalid_field",
                    "Invalid project field",
                )
                for item in error.errors(
                    include_input=False, include_context=False, include_url=False
                )
            )
            raise VersioningError("invalid_content", "Invalid project content", issues) from None
        except (ValueError, RecursionError, UnicodeError):
            raise VersioningError(
                "invalid_content", "Project must contain finite JSON values"
            ) from None

    @staticmethod
    def _revision_number(number: int) -> None:
        if type(number) is not int or number < 1 or number > 2**63 - 1:
            raise VersioningError("invalid_request", "Invalid revision number")

    @staticmethod
    def _summary(summary: str) -> None:
        if not summary.strip() or len(summary) > 1000:
            raise VersioningError(
                "invalid_request", "Revision summary is required and limited to 1000 characters"
            )

    def create(
        self,
        scope: Scope,
        project_id: str,
        name: str,
        content_json: str,
        summary: str = "Created project",
    ) -> Revision:
        actor = self._authorize(scope, project_id, True)
        self._summary(summary)
        if not name.strip() or len(name) > 200:
            raise VersioningError(
                "invalid_request", "Project name is required and limited to 200 characters"
            )
        content = self._content(content_json)
        return self.repository.create(
            scope, project_id, name, canonical(content.model_dump(mode="json")), summary, actor
        )

    def save(
        self, scope: Scope, project_id: str, expected_revision: int, content_json: str, summary: str
    ) -> Revision:
        actor = self._authorize(scope, project_id, True)
        self._summary(summary)
        self._revision_number(expected_revision)
        content = self._content(content_json)
        return self.repository.save(
            scope,
            project_id,
            expected_revision,
            canonical(content.model_dump(mode="json")),
            summary,
            actor,
        )

    def read(self, scope: Scope, project_id: str, number: int | None = None) -> Revision:
        self._authorize(scope, project_id)
        if number is not None:
            self._revision_number(number)
        return self.repository.revision(scope, project_id, number)

    def history(self, scope: Scope, project_id: str) -> tuple[Revision, ...]:
        self._authorize(scope, project_id)
        return self.repository.history(scope, project_id)

    def history_page(
        self,
        scope: Scope,
        project_id: str,
        *,
        limit: int = 50,
        before_revision: int | None = None,
    ) -> HistoryPage:
        self._authorize(scope, project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise VersioningError("invalid_request", "Invalid history pagination")
        if before_revision is not None:
            self._revision_number(before_revision)
        return self.repository.history_page(scope, project_id, limit, before_revision)

    def compare_bounded(
        self,
        scope: Scope,
        project_id: str,
        before_revision: int,
        after_revision: int,
        include_layout: bool = False,
    ) -> tuple[Change, ...]:
        self._authorize(scope, project_id)
        self._revision_number(before_revision)
        self._revision_number(after_revision)
        if type(include_layout) is not bool:
            raise VersioningError("invalid_request", "Invalid layout option")
        return compare_json(
            self.repository.revision(scope, project_id, before_revision).content_json,
            self.repository.revision(scope, project_id, after_revision).content_json,
            include_layout,
            max_changes=1000,
            max_bytes=262144,
        )

    def compare(
        self,
        scope: Scope,
        project_id: str,
        before_revision: int,
        after_revision: int,
        include_layout: bool = False,
    ) -> tuple[Change, ...]:
        self._authorize(scope, project_id)
        self._revision_number(before_revision)
        self._revision_number(after_revision)
        return compare_json(
            self.repository.revision(scope, project_id, before_revision).content_json,
            self.repository.revision(scope, project_id, after_revision).content_json,
            include_layout,
        )

    def restore(
        self,
        scope: Scope,
        project_id: str,
        revision_number: int,
        expected_revision: int,
        summary: str = "Restored revision",
    ) -> Revision:
        actor = self._authorize(scope, project_id, True)
        self._summary(summary)
        self._revision_number(revision_number)
        self._revision_number(expected_revision)
        old = self.repository.revision(scope, project_id, revision_number)
        return self.repository.save(
            scope, project_id, expected_revision, old.content_json, summary, actor
        )

    def publish(
        self, scope: Scope, project_id: str, source_revision: int, idempotency_key: str
    ) -> Release:
        actor = self._authorize(scope, project_id, True)
        self._revision_number(source_revision)
        try:
            TypeAdapter(Identifier).validate_python(idempotency_key)
        except ValidationError:
            raise VersioningError("invalid_request", "Invalid publication key") from None
        previous = self.repository.published(scope, project_id, idempotency_key)
        if previous is not None:
            if previous.source_revision != source_revision:
                raise VersioningError(
                    "conflict", "Publication key already used for another revision"
                )
            return previous
        revision = self.repository.revision(scope, project_id, source_revision)
        content = self._content(revision.content_json)
        issues = self.validator.validate(content)
        if issues:
            raise VersioningError("validation_failed", "Project is not publishable", issues)
        pending = list(content.activity_refs)
        pending.extend(
            node.activity_ref
            for workflow in content.workflows
            for node in workflow.nodes
            if node.activity_ref is not None
        )
        resolved: dict[tuple[str, str], ActivityRef] = {}
        while pending:
            ref = pending.pop()
            key = (ref.activity_id, ref.version)
            if key in resolved:
                if resolved[key] != ref:
                    raise VersioningError("dependency_conflict", "Conflicting activity digests")
                continue
            if len(resolved) >= 1000:
                raise VersioningError("dependency_limit", "Activity dependency limit exceeded")
            try:
                artifact = self.artifacts.resolve(ref)
            except OSError:
                raise VersioningError("unavailable", "Activity catalog is unavailable") from None
            if artifact is None or artifact.reference != ref:
                issue = ValidationIssue(
                    "/activity_refs",
                    "missing_dependency",
                    "Pinned activity artifact is unavailable",
                )
                raise VersioningError("validation_failed", "Project is not publishable", (issue,))
            resolved[key] = ref
            if (
                len(artifact.dependencies) > 1000
                or len(pending) + len(artifact.dependencies) > 100_000
            ):
                raise VersioningError("dependency_limit", "Activity dependency limit exceeded")
            pending.extend(artifact.dependencies)
        manifest = [ref.model_dump(mode="json") for _, ref in sorted(resolved.items())]
        artifacts_json = canonical(manifest)
        return self.repository.publish(
            scope,
            project_id,
            source_revision,
            idempotency_key,
            artifacts_json,
            digest({"content": content.model_dump(mode="json"), "artifacts": manifest}),
            digest({"content": executable(content), "artifacts": manifest}),
            actor,
        )

    def get_release(self, scope: Scope, project_id: str, release_id: str) -> Release:
        self._authorize(scope, project_id)
        return self.repository.release(scope, project_id, release_id)

    def delete_release(self, scope: Scope, project_id: str, release_id: str) -> None:
        self._authorize(scope, project_id, True)
        self.repository.delete_release(scope, project_id, release_id)
