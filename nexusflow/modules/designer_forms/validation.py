"""Composition entry point for offline publication authoring checks."""

from nexusflow.contracts.validation import ValidationIssue
from nexusflow.modules.designer_forms.form_definition import validate_form_definition
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
        form_issues = tuple(
            ValidationIssue(f"/forms/{index}{issue.path}", issue.code, issue.message)
            for index, form in enumerate(content.forms)
            for issue in validate_form_definition(form)
        )
        return form_issues + validate_graph(content) + validate_mappings(content, self.catalog)
