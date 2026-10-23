"""Composition entry point for offline publication authoring checks."""

from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.designer_forms.graph import validate_graph
from nexusflow.modules.designer_forms.mappings import validate_mappings
from nexusflow.modules.designer_forms.types import DescriptorCatalog
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator


class AuthoringValidator:
    def __init__(self, catalog: DescriptorCatalog) -> None:
        self.catalog = catalog

    def validate(self, content: ProjectContent) -> tuple[ValidationIssue, ...]:
        baseline = BuiltinContentValidator().validate(content)
        # Malformed graph/resource references cannot safely enter deeper analyses.
        if baseline:
            return baseline
        return validate_graph(content) + validate_mappings(content, self.catalog)
