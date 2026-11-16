"""Critical-path form starts against real SQLite and start-port fakes."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.form_start_types import FormStartError
from nexusflow.modules.durable_runtime.form_starts import (
    FormStartService,
    SQLiteFormStartRepository,
)
from nexusflow.modules.durable_runtime.starts import SQLiteStartRepository, StartService
from nexusflow.modules.durable_runtime.types import ReleaseBinding

FORM = {
    "form_id": "start-request",
    "schema_version": "0.1",
    "data_schema": {
        "type": "object",
        "properties": {"amount": {"type": "number"}, "reason": {"type": "string"}},
        "required": ["amount"],
        "additionalProperties": False,
    },
}


class Authorization:
    denied = False

    def authorize(self, scope, project):
        if self.denied:
            raise PermissionError("Denied")


class Releases:
    def __init__(self):
        self.binding = ReleaseBinding("acme", "dev", "release-a", "sha256:" + "a" * 64, "cfg-a")
        self.definition = (
            Path(__file__).parents[1] / "fixtures/purchasing.workflow.json"
        ).read_text()

    def resolve(self, scope, project, workflow):
        return self.binding, self.definition


class Dispatcher:
    def __init__(self):
        self.executions = {}
        self.calls = 0

    def start(self, execution_id, plan):
        self.calls += 1
        previous = self.executions.setdefault(execution_id, plan)
        assert previous == plan


class Clock:
    def __init__(self, instant: datetime):
        self.instant = instant

    def now(self):
        return self.instant


@pytest.fixture
def setup(tmp_path):
    forms = SQLiteFormStartRepository(tmp_path / "form-starts.db")
    starts = SQLiteStartRepository(tmp_path / "starts.db")
    authorization, releases, dispatcher = Authorization(), Releases(), Dispatcher()
    start_service = StartService(starts, authorization, releases, dispatcher)
    clock = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
    service = FormStartService(forms, authorization, start_service, starts, clock)
    scope = Scope(organization_id="acme", environment_id="dev")
    yield service, scope, clock, dispatcher, starts, tmp_path
    forms.close()
    starts.close()


def test_create_list_disable_and_invalid_definition(setup):
    service, scope, _, _, _, _ = setup
    record = service.create(
        scope=scope,
        project_id="hr",
        form_start_id="intake",
        workflow_id="purchase-requisition",
        form_definition_json=json.dumps(FORM),
    )
    assert record.definition.enabled is True
    assert [item.definition.form_start_id for item in service.list_form_starts(scope, "hr")] == [
        "intake"
    ]
    disabled = service.disable(scope, "hr", "intake")
    assert disabled.definition.enabled is False
    with pytest.raises(FormStartError) as error:
        service.create(
            scope=scope,
            project_id="hr",
            form_start_id="bad",
            workflow_id="purchase-requisition",
            form_definition_json=json.dumps({"form_id": "x", "schema_version": "0.1"}),
        )
    assert error.value.code == "invalid_definition"


def test_submit_uses_form_source_and_survives_restart(setup):
    service, scope, clock, dispatcher, starts, tmp_path = setup
    service.create(
        scope=scope,
        project_id="hr",
        form_start_id="intake",
        workflow_id="purchase-requisition",
        form_definition_json=json.dumps(FORM),
    )
    receipt = service.submit(
        scope,
        "hr",
        "intake",
        idempotency_key="form-1",
        values={"amount": 10, "reason": "supplies"},
    )
    assert receipt.dispatched
    assert dispatcher.calls == 1
    row = starts._db.execute(
        "SELECT source, key FROM runtime_starts WHERE execution=?", (receipt.execution_id,)
    ).fetchone()
    assert row == ("form", "form-1")
    again = service.submit(
        scope,
        "hr",
        "intake",
        idempotency_key="form-1",
        values={"amount": 10, "reason": "supplies"},
    )
    assert again.execution_id == receipt.execution_id
    assert dispatcher.calls == 1
    reopened_forms = SQLiteFormStartRepository(tmp_path / "form-starts.db")
    reopened_starts = SQLiteStartRepository(tmp_path / "starts.db")
    try:
        restarted = FormStartService(
            reopened_forms,
            Authorization(),
            StartService(reopened_starts, Authorization(), Releases(), Dispatcher()),
            reopened_starts,
            clock,
        )
        third = restarted.submit(
            scope,
            "hr",
            "intake",
            idempotency_key="form-1",
            values={"amount": 10, "reason": "supplies"},
        )
        assert third.execution_id == receipt.execution_id
    finally:
        reopened_forms.close()
        reopened_starts.close()


def test_schema_validation_and_payload_conflict(setup):
    service, scope, _, dispatcher, _, _ = setup
    service.create(
        scope=scope,
        project_id="hr",
        form_start_id="intake",
        workflow_id="purchase-requisition",
        form_definition_json=json.dumps(FORM),
    )
    with pytest.raises(FormStartError) as invalid:
        service.submit(scope, "hr", "intake", idempotency_key="bad", values={"reason": "only"})
    assert invalid.value.code == "invalid_input"
    assert dispatcher.calls == 0
    service.submit(
        scope, "hr", "intake", idempotency_key="form-1", values={"amount": 1, "reason": "a"}
    )
    with pytest.raises(FormStartError) as conflict:
        service.submit(
            scope, "hr", "intake", idempotency_key="form-1", values={"amount": 2, "reason": "b"}
        )
    assert conflict.value.code == "conflict"
