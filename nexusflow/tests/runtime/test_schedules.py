"""Critical-path schedule starts against real SQLite and start-port fakes."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.schedule_types import (
    ScheduleDefinition,
    ScheduleError,
    occurrence_idempotency_key,
)
from nexusflow.modules.durable_runtime.schedules import (
    CronExpression,
    ScheduleService,
    SQLiteScheduleRepository,
    iter_occurrences,
)
from nexusflow.modules.durable_runtime.starts import (
    SQLiteStartRepository,
    StartError,
    StartRequest,
    StartService,
)
from nexusflow.modules.durable_runtime.types import ReleaseBinding


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
        self.lose_ack = False

    def start(self, execution_id, plan):
        self.calls += 1
        previous = self.executions.setdefault(execution_id, plan)
        assert previous == plan
        if self.lose_ack:
            raise OSError("ack lost after start")


class Clock:
    def __init__(self, instant: datetime):
        self.instant = instant

    def now(self):
        return self.instant


@pytest.fixture
def setup(tmp_path):
    schedules = SQLiteScheduleRepository(tmp_path / "schedules.db")
    starts = SQLiteStartRepository(tmp_path / "starts.db")
    authorization, releases, dispatcher = Authorization(), Releases(), Dispatcher()
    start_service = StartService(starts, authorization, releases, dispatcher)
    clock = Clock(datetime(2026, 11, 15, 14, 0, tzinfo=UTC))
    service = ScheduleService(schedules, authorization, start_service, starts, clock)
    scope = Scope(organization_id="acme", environment_id="dev")
    yield service, scope, clock, dispatcher, starts, tmp_path
    schedules.close()
    starts.close()


def test_cron_timezone_and_occurrence_key():
    cron = CronExpression.parse("0 9 * * 1-5")
    due = iter_occurrences(
        cron,
        "America/New_York",
        datetime(2026, 11, 13, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 14, 20, 0, tzinfo=UTC),
    )
    # Friday 13 Nov 2026 09:00 America/New_York = 14:00 UTC
    assert due == [datetime(2026, 11, 13, 14, 0, tzinfo=UTC)]
    assert occurrence_idempotency_key("nightly", due[0]) == "nightly.20261113T140000Z"
    with pytest.raises(ScheduleError) as error:
        CronExpression.parse("0 9 * * mon")
    assert error.value.code == "invalid_input"


def test_create_list_disable_and_invalid_timezone(setup):
    service, scope, clock, *_ = setup
    definition = ScheduleDefinition(
        schedule_id="nightly",
        scope=scope,
        project_id="hr",
        workflow_id="purchase-requisition",
        cron="0 * * * *",
        timezone="UTC",
        overlap_policy="allow",
        input_json='{"amount":1}',
    )
    created = service.create(definition)
    assert created.definition.enabled is True
    assert service.list_schedules(scope, "hr") == (created,)
    with pytest.raises(ScheduleError) as error:
        service.create(
            ScheduleDefinition(
                schedule_id="bad",
                scope=scope,
                project_id="hr",
                workflow_id="purchase-requisition",
                cron="0 * * * *",
                timezone="Not/AZone",
                overlap_policy="allow",
            )
        )
    assert error.value.code == "invalid_timezone"
    disabled = service.disable(scope, "hr", "nightly")
    assert disabled.definition.enabled is False
    assert disabled.disabled_at == clock.instant


def test_fire_due_uses_schedule_source_and_survives_restart(setup):
    service, scope, clock, dispatcher, starts, path = setup
    service.create(
        ScheduleDefinition(
            schedule_id="hourly",
            scope=scope,
            project_id="hr",
            workflow_id="purchase-requisition",
            cron="0 * * * *",
            timezone="UTC",
            overlap_policy="allow",
            input_json="{}",
        )
    )
    # One hour after create cursor (14:00) so 15:00 is due.
    clock.instant = datetime(2026, 11, 15, 15, 0, tzinfo=UTC)
    assert service.fire_due(scope) == 1
    assert dispatcher.calls == 1
    key = "hourly.20261115T150000Z"
    fingerprint = hashlib.sha256(
        json.dumps({}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    receipt = starts.lookup(
        StartRequest(scope, "hr", "purchase-requisition", key, source="schedule"),
        fingerprint,
    )
    assert receipt is not None and receipt.dispatched
    assert service.fire_due(scope) == 0
    assert dispatcher.calls == 1

    reopened_schedules = SQLiteScheduleRepository(path / "schedules.db")
    reopened_starts = SQLiteStartRepository(path / "starts.db")
    try:
        recovered = ScheduleService(
            reopened_schedules,
            service.authorization,
            StartService(
                reopened_starts, service.authorization, service.starts.releases, dispatcher
            ),
            reopened_starts,
            clock,
        )
        assert recovered.fire_due(scope) == 0
        assert dispatcher.calls == 1
    finally:
        reopened_schedules.close()
        reopened_starts.close()


def test_skip_overlap_while_prior_start_undelivered(setup):
    service, scope, clock, dispatcher, *_ = setup
    service.create(
        ScheduleDefinition(
            schedule_id="busy",
            scope=scope,
            project_id="hr",
            workflow_id="purchase-requisition",
            cron="* * * * *",
            timezone="UTC",
            overlap_policy="skip",
        )
    )
    clock.instant = datetime(2026, 11, 15, 14, 1, tzinfo=UTC)
    dispatcher.lose_ack = True
    assert service.fire_due(scope) == 0
    assert dispatcher.calls == 1
    clock.instant = datetime(2026, 11, 15, 14, 2, tzinfo=UTC)
    # Prior occurrence intent remains undispatched; next minute is skipped.
    assert service.fire_due(scope) == 0
    assert dispatcher.calls == 1
    dispatcher.lose_ack = False
    service.starts.start(
        StartRequest(
            scope, "hr", "purchase-requisition", "busy.20261115T140100Z", source="schedule"
        )
    )
    assert dispatcher.calls == 2
    clock.instant = datetime(2026, 11, 15, 14, 3, tzinfo=UTC)
    assert service.fire_due(scope) >= 1


def test_conflicting_payload_on_occurrence_key(setup):
    service, scope, clock, dispatcher, *_ = setup
    service.create(
        ScheduleDefinition(
            schedule_id="fixed",
            scope=scope,
            project_id="hr",
            workflow_id="purchase-requisition",
            cron="0 15 * * *",
            timezone="UTC",
            overlap_policy="allow",
            input_json='{"a":1}',
        )
    )
    clock.instant = datetime(2026, 11, 15, 15, 0, tzinfo=UTC)
    assert service.fire_due(scope) == 1
    with pytest.raises(StartError) as error:
        service.starts.start(
            StartRequest(
                scope,
                "hr",
                "purchase-requisition",
                "fixed.20261115T150000Z",
                '{"a":2}',
                source="schedule",
            )
        )
    assert error.value.code == "conflict"
    assert dispatcher.calls == 1
