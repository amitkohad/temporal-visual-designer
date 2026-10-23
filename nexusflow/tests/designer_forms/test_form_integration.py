"""Form publication integration and privacy boundary regressions."""

import json
from dataclasses import replace

import pytest

from nexusflow.modules.designer_forms.form_types import FormError
from nexusflow.modules.designer_forms.forms import FormService
from nexusflow.modules.designer_forms.validation import AuthoringValidator
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.tests.designer_forms.test_acceptance import Catalog, project
from nexusflow.tests.designer_forms.test_forms import SCOPE, Gateway, definition


def test_publication_reports_located_unsupported_form_definition():
    payload = project()
    form = definition(attachment_policy={"maximum_files": 10})
    payload["forms"] = [form]
    issues = AuthoringValidator(Catalog()).validate(
        ProjectContent.model_validate_json(json.dumps(payload))
    )
    assert any(issue.path.startswith("/forms/0/attachment_policy") for issue in issues)


def test_native_schema_field_names_are_not_mistaken_for_secret_values():
    payload = project()
    form = definition()
    form["data_schema"]["properties"]["password"] = {"type": "string", "writeOnly": True}
    form["validation_rules"] = [
        {"schema_version": "0.1", "data_schema": {"properties": {"password": {"minLength": 8}}}}
    ]
    form["field_permissions"] = {"password": {"read_only": True}}
    payload["forms"] = [form]
    assert (
        AuthoringValidator(Catalog()).validate(
            ProjectContent.model_validate_json(json.dumps(payload))
        )
        == ()
    )


def test_dependent_schema_required_hint_matches_submission_rule():
    form = definition()
    form["data_schema"]["required"] = []
    form["data_schema"]["dependentSchemas"] = {"name": {"required": ["amount"]}}
    gateway = Gateway(form=form)
    view = FormService(gateway).preview(SCOPE, "task")
    assert next(field for field in view.fields if field.field_id == "amount").required
    values = json.loads(gateway.snapshot.current_values_json)
    del values["amount"]
    gateway.snapshot = replace(gateway.snapshot, current_values_json=json.dumps(values))
    with pytest.raises(FormError) as error:
        FormService(gateway).submit(SCOPE, "task", 3, "{}")
    assert error.value.code == "validation_failed"
    assert gateway.commits == []


@pytest.mark.parametrize(
    "payload",
    ['{"name":"first","name":"second"}', '{"name":NaN}', '{"name":"\\ud800"}', '{"name":Infinity}'],
)
def test_ambiguous_or_nonportable_json_is_rejected_before_commit(payload):
    gateway = Gateway()
    with pytest.raises(FormError) as error:
        FormService(gateway).submit(SCOPE, "task", 3, payload)
    assert error.value.code == "invalid_input"
    assert gateway.commits == []


def test_hidden_or_unreadable_values_do_not_affect_preview_visibility():
    form = definition(
        visibility_rules=[
            {
                "schema_version": "0.1",
                "field_id": "amount",
                "when": {
                    "properties": {"private": {"const": "HIDDEN-SECRET"}},
                    "required": ["private"],
                },
            }
        ]
    )
    gateway = Gateway(form=form)
    service = FormService(gateway)
    first = service.preview(SCOPE, "task")
    values = json.loads(gateway.snapshot.current_values_json)
    values["private"] = "DIFFERENT-SECRET"
    gateway.snapshot = replace(gateway.snapshot, current_values_json=json.dumps(values))
    assert service.preview(SCOPE, "task") == first


def test_form_contract_exports_are_portable_and_deterministic(tmp_path, monkeypatch):
    from jsonschema import Draft202012Validator

    from nexusflow.modules.designer_forms.form_export import main

    monkeypatch.chdir(tmp_path)
    main()
    target = tmp_path / "artifacts/schemas/0.1"
    first = {path.name: path.read_bytes() for path in target.glob("*.json")}
    assert set(first) == {
        "form-view.schema.json",
        "form-visibility-rule.schema.json",
        "form-validation-rule.schema.json",
    }
    for data in first.values():
        Draft202012Validator.check_schema(json.loads(data))
    main()
    assert {path.name: path.read_bytes() for path in target.glob("*.json")} == first
