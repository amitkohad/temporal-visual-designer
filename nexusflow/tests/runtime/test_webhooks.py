"""Critical-path webhook starts against real SQLite and start-port fakes."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexusflow.contracts import Scope
from nexusflow.modules.durable_runtime.starts import (
    SQLiteStartRepository,
    StartError,
    StartRequest,
    StartService,
)
from nexusflow.modules.durable_runtime.types import ReleaseBinding
from nexusflow.modules.durable_runtime.webhook_types import WebhookError, digest_webhook_secret
from nexusflow.modules.durable_runtime.webhooks import SQLiteWebhookRepository, WebhookService


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
    webhooks = SQLiteWebhookRepository(tmp_path / "webhooks.db")
    starts = SQLiteStartRepository(tmp_path / "starts.db")
    authorization, releases, dispatcher = Authorization(), Releases(), Dispatcher()
    start_service = StartService(starts, authorization, releases, dispatcher)
    clock = Clock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
    service = WebhookService(webhooks, authorization, start_service, starts, clock)
    scope = Scope(organization_id="acme", environment_id="dev")
    yield service, scope, clock, dispatcher, starts, tmp_path
    webhooks.close()
    starts.close()


def test_secret_digest_and_create_list_disable(setup):
    service, scope, _, _, _, _ = setup
    digest = digest_webhook_secret("s3cret")
    assert len(digest) == 64
    record = service.create(
        scope=scope,
        project_id="hr",
        webhook_id="inbound",
        workflow_id="purchase-requisition",
        secret="s3cret",
        replay_window_seconds=120,
    )
    assert record.definition.secret_digest == digest
    assert record.definition.replay_window_seconds == 120
    listed = service.list_webhooks(scope, "hr")
    assert [item.definition.webhook_id for item in listed] == ["inbound"]
    disabled = service.disable(scope, "hr", "inbound")
    assert disabled.definition.enabled is False
    assert disabled.disabled_at is not None


def test_deliver_uses_webhook_source_and_survives_restart(setup):
    service, scope, clock, dispatcher, starts, tmp_path = setup
    service.create(
        scope=scope,
        project_id="hr",
        webhook_id="inbound",
        workflow_id="purchase-requisition",
        secret="s3cret",
    )
    stamp = str(int(clock.now().timestamp()))
    receipt = service.deliver(
        scope,
        "inbound",
        secret="s3cret",
        timestamp=stamp,
        delivery_id="delivery-1",
        input_json='{"amount":1}',
    )
    assert receipt.dispatched
    assert dispatcher.calls == 1
    row = starts._db.execute(
        "SELECT source, key FROM runtime_starts WHERE execution=?", (receipt.execution_id,)
    ).fetchone()
    assert row == ("webhook", "delivery-1")
    again = service.deliver(
        scope,
        "inbound",
        secret="s3cret",
        timestamp=stamp,
        delivery_id="delivery-1",
        input_json='{"amount":1}',
    )
    assert again.execution_id == receipt.execution_id
    assert dispatcher.calls == 1
    reopened_webhooks = SQLiteWebhookRepository(tmp_path / "webhooks.db")
    reopened_starts = SQLiteStartRepository(tmp_path / "starts.db")
    try:
        restarted = WebhookService(
            reopened_webhooks,
            Authorization(),
            StartService(reopened_starts, Authorization(), Releases(), Dispatcher()),
            reopened_starts,
            clock,
        )
        third = restarted.deliver(
            scope,
            "inbound",
            secret="s3cret",
            timestamp=stamp,
            delivery_id="delivery-1",
            input_json='{"amount":1}',
        )
        assert third.execution_id == receipt.execution_id
    finally:
        reopened_webhooks.close()
        reopened_starts.close()


def test_invalid_secret_and_replay_reject_without_start(setup):
    service, scope, clock, dispatcher, _, _ = setup
    service.create(
        scope=scope,
        project_id="hr",
        webhook_id="inbound",
        workflow_id="purchase-requisition",
        secret="s3cret",
        replay_window_seconds=60,
    )
    with pytest.raises(WebhookError) as bad_secret:
        service.deliver(
            scope,
            "inbound",
            secret="wrong",
            timestamp=str(int(clock.now().timestamp())),
            delivery_id="delivery-bad",
        )
    assert bad_secret.value.code == "unauthenticated"
    clock.instant = clock.instant + timedelta(seconds=120)
    with pytest.raises(WebhookError) as replay:
        service.deliver(
            scope,
            "inbound",
            secret="s3cret",
            timestamp=str(int((clock.instant - timedelta(seconds=120)).timestamp())),
            delivery_id="delivery-late",
        )
    assert replay.value.code == "invalid_input"
    assert dispatcher.calls == 0


def test_conflicting_payload_on_delivery_key(setup):
    service, scope, clock, _, starts, _ = setup
    service.create(
        scope=scope,
        project_id="hr",
        webhook_id="inbound",
        workflow_id="purchase-requisition",
        secret="s3cret",
    )
    stamp = str(int(clock.now().timestamp()))
    service.deliver(
        scope,
        "inbound",
        secret="s3cret",
        timestamp=stamp,
        delivery_id="delivery-1",
        input_json='{"amount":1}',
    )
    with pytest.raises(WebhookError) as error:
        service.deliver(
            scope,
            "inbound",
            secret="s3cret",
            timestamp=stamp,
            delivery_id="delivery-1",
            input_json='{"amount":2}',
        )
    assert error.value.code == "conflict"
    # Direct start conflict still holds on the ledger.
    with pytest.raises(StartError) as start_error:
        StartService(starts, Authorization(), Releases(), Dispatcher()).start(
            StartRequest(
                scope,
                "hr",
                "purchase-requisition",
                "delivery-1",
                json.dumps({"amount": 3}),
                source="webhook",
            )
        )
    assert start_error.value.code == "conflict"
