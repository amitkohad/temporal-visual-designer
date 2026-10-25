"""Proof-runtime graph and actual catalog checks without executing bundle code."""

import json

from pydantic import JsonValue, TypeAdapter

from nexusflow.contracts.models import ActivityRef, Scope
from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.activity_catalog.catalog_types import CatalogRepository, RuntimeCapabilities
from nexusflow.modules.activity_catalog.types import ActivityManifest, CatalogError
from nexusflow.modules.durable_runtime.preflight import (
    MAX_DEFINITION_BYTES,
    PlanError,
    validate_plan_structure,
)
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.package_promotion.types import ActivationRequest, ConfigurationCompatibility
from nexusflow.modules.project_versioning.types import ProjectContent, Release


class ProofActivationReadiness:
    def __init__(
        self,
        catalog: CatalogRepository,
        capabilities: RuntimeCapabilities,
        configurations: ConfigurationCompatibility,
    ) -> None:
        self.catalog = catalog
        self.capabilities = capabilities
        self.configurations = configurations

    def validate(
        self, scope: Scope, release: Release, request: ActivationRequest
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        content = ProjectContent.model_validate_json(release.content_json)
        if (
            self.configurations.supports(
                scope,
                request.project_id,
                request.configuration_revision,
                content.configuration_schema,
            )
            is not True
        ):
            issues.append(
                ValidationIssue(
                    "/configuration_revision",
                    "invalid_configuration",
                    "Destination configuration is unavailable or invalid",
                )
            )
        binding = ReleaseBinding(
            scope.organization_id,
            scope.environment_id,
            release.release_id,
            release.executable_digest,
            request.configuration_revision,
            request.runtime_version,
        )
        for index, workflow in enumerate(content.workflows):
            original_json = json.dumps(
                workflow.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(original_json.encode()) > MAX_DEFINITION_BYTES:
                issues.append(
                    ValidationIssue(
                        f"/workflows/{index}",
                        "definition_limit",
                        "Workflow exceeds the selected interpreter's definition budget",
                    )
                )

            def references(value: JsonValue) -> bool:
                if isinstance(value, dict):
                    return (
                        "$ref" in value
                        or "$dynamicRef" in value
                        or any(references(child) for child in value.values())
                    )
                return isinstance(value, list) and any(references(child) for child in value)

            if references(workflow.input_schema):
                issues.append(
                    ValidationIssue(
                        f"/workflows/{index}/input_schema",
                        "unsupported_schema",
                        "Referenced input schemas are unsupported by this interpreter",
                    )
                )
            # Activation checks structure without inventing inputs for schemas
            # requiring business data. Actual starts validate the original schema.
            try:
                validate_plan_structure(original_json, binding, scope)
            except PlanError:
                issues.append(
                    ValidationIssue(
                        f"/workflows/{index}",
                        "unsupported_workflow",
                        "Workflow is not executable by the selected interpreter",
                    )
                )
        for index, ref in enumerate(
            TypeAdapter(tuple[ActivityRef, ...]).validate_json(release.artifacts_json)
        ):
            try:
                entry = self.catalog.get(scope, ref)
                manifest = ActivityManifest.model_validate_json(entry.manifest_json)
                if (
                    entry.scope != scope
                    or entry.reference != ref
                    or manifest.descriptor.activity_ref != ref
                    or entry.state == "withdrawn"
                ):
                    raise CatalogError("not_found", "Activity is unavailable")
                if self.capabilities.supports(scope, manifest) is not True:
                    issues.append(
                        ValidationIssue(
                            f"/artifacts/{index}",
                            "incompatible_activity",
                            "Destination worker cannot execute the pinned activity",
                        )
                    )
            except CatalogError as error:
                if error.code not in {"not_found", "withdrawn"}:
                    raise OSError("Destination catalog is unavailable") from None
                issues.append(
                    ValidationIssue(
                        f"/artifacts/{index}",
                        "missing_activity",
                        "Pinned activity is unavailable in the destination",
                    )
                )
        return tuple(issues)

    def retain(self, scope: Scope, release: Release, usage_id: str) -> None:
        for ref in TypeAdapter(tuple[ActivityRef, ...]).validate_json(release.artifacts_json):
            try:
                self.catalog.retain(scope, ref, usage_id)
            except CatalogError:
                raise OSError("Destination activity retention is unavailable") from None
