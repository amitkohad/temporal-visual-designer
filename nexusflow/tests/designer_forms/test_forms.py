"""Independent acceptance tests for authorized server form preview and submission."""

import json
from dataclasses import replace

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.designer_forms.form_types import AuthorizedForm, FormError
from nexusflow.modules.designer_forms.forms import FormService

SCOPE = Scope(organization_id="acme", environment_id="dev")


def definition(**overrides):
    result = {
        "schema_version": "0.1",
        "form_id": "purchase",
        "data_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "title": "Employee name"},
                "amount": {"type": "number", "minimum": 1, "maximum": 1000},
                "approved": {"type": "boolean", "readOnly": True},
                "token": {"type": "string", "writeOnly": True},
                "private": {"type": "string"},
            },
            "required": ["name", "amount"],
            "additionalProperties": False,
        },
        "layout": {"field_order": ["amount", "name", "approved", "token", "private"]},
    }
    result.update(overrides)
    return result


class Gateway:
    def __init__(self, form=None, values=None):
        self.snapshot = AuthorizedForm(
            definition_json=json.dumps(form or definition()),
            current_values_json=json.dumps(
                values
                or {
                    "name": "Ada",
                    "amount": 10,
                    "approved": False,
                    "token": "TOP-SECRET",
                    "private": "HIDDEN-SECRET",
                }
            ),
            revision=3,
            readable_fields=frozenset({"name", "amount", "approved", "token"}),
            editable_fields=frozenset({"name", "amount", "approved", "token"}),
        )
        self.calls = []
        self.commits = []
        self.revoked = False
        self.revoke_on_commit = False

    def authorize(self, scope, resource_id, action):
        self.calls.append((scope, resource_id, action))
        if self.revoked or scope != SCOPE:
            raise FormError("forbidden", "Permission denied")
        return self.snapshot

    def commit(self, scope, resource_id, expected_revision, values_json):
        if self.revoked or self.revoke_on_commit:
            raise FormError("forbidden", "Permission denied")
        if expected_revision != self.snapshot.revision:
            raise FormError("conflict", "Revision changed")
        self.commits.append(json.loads(values_json))
        self.snapshot = replace(
            self.snapshot, current_values_json=values_json, revision=expected_revision + 1
        )
        return self.snapshot.revision


def test_preview_orders_fields_and_redacts_unreadable_and_write_only_values():
    gateway = Gateway()
    view = FormService(gateway).preview(SCOPE, "task-1")
    assert view.revision == 3
    assert [field.field_id for field in view.fields] == ["amount", "name", "approved", "token"]
    assert json.loads(view.values_json) == {"name": "Ada", "amount": 10, "approved": False}
    assert "TOP-SECRET" not in repr(view)
    assert "HIDDEN-SECRET" not in repr(view)
    assert next(field for field in view.fields if field.field_id == "approved").editable is False
    assert next(field for field in view.fields if field.field_id == "token").write_only is True


def test_submit_merges_partial_patch_and_preserves_server_owned_values():
    gateway = Gateway()
    assert FormService(gateway).submit(SCOPE, "task-1", 3, '{"amount":25}') == 4
    assert gateway.commits == [
        {
            "name": "Ada",
            "amount": 25,
            "approved": False,
            "token": "TOP-SECRET",
            "private": "HIDDEN-SECRET",
        }
    ]
    assert gateway.calls == [(SCOPE, "task-1", "submit")]


@pytest.mark.parametrize(
    "values",
    [
        '{"name":""}',
        '{"amount":0}',
        '{"amount":1001}',
        '{"amount":"SECRET-BAD-VALUE"}',
        '{"name":null}',
    ],
)
def test_invalid_submission_has_located_sanitized_issues_and_no_write(values):
    gateway = Gateway()
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, values)
    assert caught.value.code == "validation_failed"
    assert caught.value.issues
    assert all(issue.path for issue in caught.value.issues)
    assert "SECRET-BAD-VALUE" not in str(caught.value)
    assert "SECRET-BAD-VALUE" not in repr(caught.value.issues)
    assert gateway.commits == []


@pytest.mark.parametrize(
    "values",
    ['{"approved":true}', '{"approved":false}', '{"private":"guess"}', '{"unknown":"value"}'],
)
def test_client_cannot_supply_read_only_unreadable_or_unknown_fields(values):
    gateway = Gateway()
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, values)
    assert caught.value.code in {"forbidden", "validation_failed"}
    assert gateway.commits == []


@pytest.mark.parametrize(
    "payload", ["null", "[]", "true", "{", '{"amount":NaN}', '{"amount":1,"amount":2}']
)
def test_rejects_invalid_nonobject_or_ambiguous_input(payload):
    gateway = Gateway()
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, payload)
    assert caught.value.code == "invalid_input"
    assert gateway.commits == []


def test_visibility_uses_safe_data_and_hidden_fields_cannot_be_submitted():
    gateway = Gateway(
        form=definition(
            visibility_rules=[
                {
                    "schema_version": "0.1",
                    "field_id": "amount",
                    "when": {"properties": {"name": {"const": "Grace"}}, "required": ["name"]},
                }
            ]
        )
    )
    service = FormService(gateway)
    assert "amount" not in [field.field_id for field in service.preview(SCOPE, "task-1").fields]
    with pytest.raises(FormError) as caught:
        service.submit(SCOPE, "task-1", 3, '{"amount":20}')
    assert caught.value.code == "forbidden"
    assert not gateway.commits
    assert service.submit(SCOPE, "task-1", 3, '{"name":"Grace","amount":20}') == 4


def test_hidden_required_field_still_requires_valid_value():
    gateway = Gateway(
        form=definition(
            visibility_rules=[{"schema_version": "0.1", "field_id": "amount", "when": {"not": {}}}]
        ),
        values={"name": "Ada"},
    )
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, '{"name":"Grace"}')
    assert caught.value.code == "validation_failed"
    assert not gateway.commits


def test_conditional_validation_rule_applies_to_merged_values():
    gateway = Gateway(
        form=definition(
            validation_rules=[
                {
                    "schema_version": "0.1",
                    "data_schema": {
                        "if": {"properties": {"name": {"const": "Manager"}}, "required": ["name"]},
                        "then": {"properties": {"amount": {"minimum": 100}}},
                    },
                }
            ]
        )
    )
    service = FormService(gateway)
    with pytest.raises(FormError) as caught:
        service.submit(SCOPE, "task-1", 3, '{"name":"Manager"}')
    assert caught.value.code == "validation_failed"
    assert not gateway.commits
    assert service.submit(SCOPE, "task-1", 3, '{"name":"Manager","amount":100}') == 4


def test_authorization_is_fresh_on_every_preview_and_submit():
    gateway = Gateway()
    service = FormService(gateway)
    service.preview(SCOPE, "task-1")
    gateway.revoked = True
    with pytest.raises(FormError, match="Permission denied"):
        service.preview(SCOPE, "task-1")
    with pytest.raises(FormError, match="Permission denied"):
        service.submit(SCOPE, "task-1", 3, '{"amount":20}')
    assert [call[2] for call in gateway.calls] == ["preview", "preview", "submit"]
    assert not gateway.commits


def test_permission_revoke_during_validation_blocks_atomic_commit():
    gateway = Gateway()
    gateway.revoke_on_commit = True
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, '{"amount":20}')
    assert caught.value.code == "forbidden"
    assert not gateway.commits


def test_stale_revision_and_foreign_scope_cannot_write():
    gateway = Gateway()
    service = FormService(gateway)
    with pytest.raises(FormError) as caught:
        service.submit(SCOPE, "task-1", 2, '{"amount":20}')
    assert caught.value.code == "conflict"
    with pytest.raises(FormError) as caught:
        service.submit(
            Scope(organization_id="other", environment_id="dev"), "task-1", 3, '{"amount":20}'
        )
    assert caught.value.code == "forbidden"
    assert not gateway.commits


@pytest.mark.parametrize(
    "override",
    [
        {"attachment_policy": {"enabled": True}},
        {"layout": {"script": "arbitrary"}},
        {"visibility_rules": [{"schema_version": "9", "field_id": "amount", "when": {}}]},
    ],
)
def test_unsupported_or_invalid_definition_fails_closed(override):
    gateway = Gateway(form=definition(**override))
    with pytest.raises(FormError) as caught:
        FormService(gateway).preview(SCOPE, "task-1")
    assert caught.value.code == "invalid_definition"
    assert not gateway.commits


def test_field_permission_read_only_blocks_even_unchanged_supplied_value():
    gateway = Gateway(form=definition(field_permissions={"name": {"read_only": True}}))
    service = FormService(gateway)
    assert (
        next(
            field for field in service.preview(SCOPE, "task-1").fields if field.field_id == "name"
        ).editable
        is False
    )
    with pytest.raises(FormError) as caught:
        service.submit(SCOPE, "task-1", 3, '{"name":"Ada"}')
    assert caught.value.code == "forbidden"
    assert not gateway.commits


def test_secret_and_unreadable_values_cannot_drive_visibility():
    gateway = Gateway(
        form=definition(
            visibility_rules=[
                {
                    "schema_version": "0.1",
                    "field_id": "amount",
                    "when": {
                        "properties": {"token": {"const": "TOP-SECRET"}},
                        "required": ["token"],
                    },
                }
            ]
        )
    )
    view = FormService(gateway).preview(SCOPE, "task-1")
    assert "amount" not in [field.field_id for field in view.fields]
    assert "TOP-SECRET" not in view.values_json


def test_concurrent_change_after_authorization_is_rejected_by_commit_cas():
    class RacingGateway(Gateway):
        def commit(self, scope, resource_id, expected_revision, values_json):
            self.snapshot = replace(self.snapshot, revision=self.snapshot.revision + 1)
            return super().commit(scope, resource_id, expected_revision, values_json)

    gateway = RacingGateway()
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", 3, '{"amount":20}')
    assert caught.value.code == "conflict"
    assert not gateway.commits


@pytest.mark.parametrize("revision", [True, -1, "3", 2**63])
def test_invalid_revision_never_reaches_commit(revision):
    gateway = Gateway()
    with pytest.raises(FormError) as caught:
        FormService(gateway).submit(SCOPE, "task-1", revision, '{"amount":20}')
    assert caught.value.code == "invalid_input"
    assert not gateway.commits


def test_write_only_preview_metadata_does_not_expose_secret_defaults_or_examples():
    form = definition()
    form["data_schema"]["properties"]["token"].update(
        {"default": "SECRET-DEFAULT", "examples": ["SECRET-EXAMPLE"]}
    )
    gateway = Gateway(form=form)
    view = FormService(gateway).preview(SCOPE, "task-1")
    assert "SECRET-DEFAULT" not in repr(view)
    assert "SECRET-EXAMPLE" not in repr(view)
