"""Webhook persistence and secret-authenticated deliver into durable starts (ADR 0070)."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.durable_runtime.starts import (
    AsyncStartService,
    SQLiteStartRepository,
    StartError,
    StartReceipt,
    StartRequest,
    StartService,
)
from nexusflow.modules.durable_runtime.webhook_types import (
    WebhookAuthorization,
    WebhookClock,
    WebhookDefinition,
    WebhookDeliveryRecord,
    WebhookError,
    WebhookRecord,
    digest_webhook_secret,
    parse_delivery_timestamp,
    require_aware_utc,
)


class SQLiteWebhookRepository:
    def __init__(self, path: Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS runtime_webhooks (
                org TEXT NOT NULL, env TEXT NOT NULL, webhook TEXT NOT NULL,
                project TEXT NOT NULL, workflow TEXT NOT NULL,
                secret_digest TEXT NOT NULL, replay_window INTEGER NOT NULL,
                enabled INTEGER NOT NULL, created_at TEXT NOT NULL, disabled_at TEXT,
                PRIMARY KEY (org, env, webhook))"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS runtime_webhook_deliveries (
                org TEXT NOT NULL, env TEXT NOT NULL, webhook TEXT NOT NULL,
                delivery_id TEXT NOT NULL, received_at TEXT NOT NULL,
                status TEXT NOT NULL, execution TEXT,
                PRIMARY KEY (org, env, webhook, delivery_id))"""
            )

    def close(self) -> None:
        self._db.close()

    def create(self, record: WebhookRecord) -> WebhookRecord:
        definition = record.definition
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_webhooks
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        definition.scope.organization_id,
                        definition.scope.environment_id,
                        definition.webhook_id,
                        definition.project_id,
                        definition.workflow_id,
                        definition.secret_digest,
                        definition.replay_window_seconds,
                        1 if definition.enabled else 0,
                        require_aware_utc(record.created_at).isoformat(),
                        None
                        if record.disabled_at is None
                        else require_aware_utc(record.disabled_at).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise WebhookError("conflict", "Webhook already exists") from None
        saved = self.get(definition.scope, definition.webhook_id)
        assert saved is not None
        return saved

    def get(self, scope: Scope, webhook_id: str) -> WebhookRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT webhook,project,workflow,secret_digest,replay_window,enabled,
                created_at,disabled_at FROM runtime_webhooks
                WHERE org=? AND env=? AND webhook=?""",
                (scope.organization_id, scope.environment_id, webhook_id),
            ).fetchone()
        if row is None:
            return None
        return WebhookRecord(
            definition=WebhookDefinition(
                webhook_id=row[0],
                scope=scope,
                project_id=row[1],
                workflow_id=row[2],
                secret_digest=row[3],
                replay_window_seconds=row[4],
                enabled=bool(row[5]),
            ),
            created_at=datetime.fromisoformat(row[6]),
            disabled_at=None if row[7] is None else datetime.fromisoformat(row[7]),
        )

    def list_records(
        self, scope: Scope, project_id: str | None = None
    ) -> tuple[WebhookRecord, ...]:
        with self._lock:
            if project_id is None:
                rows = self._db.execute(
                    """SELECT webhook,project,workflow,secret_digest,replay_window,enabled,
                    created_at,disabled_at FROM runtime_webhooks
                    WHERE org=? AND env=? ORDER BY webhook""",
                    (scope.organization_id, scope.environment_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    """SELECT webhook,project,workflow,secret_digest,replay_window,enabled,
                    created_at,disabled_at FROM runtime_webhooks
                    WHERE org=? AND env=? AND project=? ORDER BY webhook""",
                    (scope.organization_id, scope.environment_id, project_id),
                ).fetchall()
        return tuple(
            WebhookRecord(
                definition=WebhookDefinition(
                    webhook_id=row[0],
                    scope=scope,
                    project_id=row[1],
                    workflow_id=row[2],
                    secret_digest=row[3],
                    replay_window_seconds=row[4],
                    enabled=bool(row[5]),
                ),
                created_at=datetime.fromisoformat(row[6]),
                disabled_at=None if row[7] is None else datetime.fromisoformat(row[7]),
            )
            for row in rows
        )

    def disable(self, scope: Scope, webhook_id: str, at: datetime) -> WebhookRecord:
        instant = require_aware_utc(at)
        with self._lock, self._db:
            existing = self.get(scope, webhook_id)
            if existing is None:
                raise WebhookError("not_found", "Webhook was not found")
            if not existing.definition.enabled:
                return existing
            self._db.execute(
                """UPDATE runtime_webhooks SET enabled=0, disabled_at=?
                WHERE org=? AND env=? AND webhook=?""",
                (
                    instant.isoformat(),
                    scope.organization_id,
                    scope.environment_id,
                    webhook_id,
                ),
            )
        saved = self.get(scope, webhook_id)
        assert saved is not None
        return saved

    def delivery(
        self, scope: Scope, webhook_id: str, delivery_id: str
    ) -> WebhookDeliveryRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT webhook,delivery_id,received_at,status,execution
                FROM runtime_webhook_deliveries
                WHERE org=? AND env=? AND webhook=? AND delivery_id=?""",
                (scope.organization_id, scope.environment_id, webhook_id, delivery_id),
            ).fetchone()
        if row is None:
            return None
        return WebhookDeliveryRecord(
            webhook_id=row[0],
            delivery_id=row[1],
            received_at=datetime.fromisoformat(row[2]),
            status=row[3],
            execution_id=row[4],
        )

    def save_delivery(self, scope: Scope, record: WebhookDeliveryRecord) -> WebhookDeliveryRecord:
        received = require_aware_utc(record.received_at)
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_webhook_deliveries
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        scope.organization_id,
                        scope.environment_id,
                        record.webhook_id,
                        record.delivery_id,
                        received.isoformat(),
                        record.status,
                        record.execution_id,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.delivery(scope, record.webhook_id, record.delivery_id)
                if existing is None:
                    raise WebhookError("conflict", "Webhook delivery conflicts") from None
                return existing
        saved = self.delivery(scope, record.webhook_id, record.delivery_id)
        assert saved is not None
        return saved


class _WebhookBoundary:
    def __init__(
        self,
        repository: SQLiteWebhookRepository,
        authorization: WebhookAuthorization,
        start_repository: SQLiteStartRepository,
        clock: WebhookClock,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.start_repository = start_repository
        self.clock = clock

    def create(
        self,
        *,
        scope: Scope,
        project_id: str,
        webhook_id: str,
        workflow_id: str,
        secret: str,
        replay_window_seconds: int = 300,
    ) -> WebhookRecord:
        self.authorization.authorize(scope, project_id)
        try:
            for value in (webhook_id, project_id, workflow_id):
                TypeAdapter(Identifier).validate_python(value)
            if type(replay_window_seconds) is not int or not 1 <= replay_window_seconds <= 3600:
                raise ValueError
            digest = digest_webhook_secret(secret)
            definition = WebhookDefinition(
                webhook_id=webhook_id,
                scope=scope,
                project_id=project_id,
                workflow_id=workflow_id,
                secret_digest=digest,
                replay_window_seconds=replay_window_seconds,
                enabled=True,
            )
        except WebhookError:
            raise
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise WebhookError("invalid_input", "Invalid webhook definition") from None
        now = require_aware_utc(self.clock.now())
        return self.repository.create(
            WebhookRecord(definition=definition, created_at=now, disabled_at=None)
        )

    def list_webhooks(self, scope: Scope, project_id: str) -> tuple[WebhookRecord, ...]:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(project_id)
        return self.repository.list_records(scope, project_id)

    def disable(self, scope: Scope, project_id: str, webhook_id: str) -> WebhookRecord:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(webhook_id)
        TypeAdapter(Identifier).validate_python(project_id)
        existing = self.repository.get(scope, webhook_id)
        if existing is None:
            raise WebhookError("not_found", "Webhook was not found")
        if existing.definition.project_id != project_id:
            raise WebhookError("not_found", "Webhook was not found")
        return self.repository.disable(scope, webhook_id, self.clock.now())

    def _authenticate(
        self, record: WebhookRecord, secret: str, timestamp_raw: str, delivery_id: str
    ) -> datetime:
        if not record.definition.enabled:
            raise WebhookError("not_found", "Webhook was not found")
        try:
            TypeAdapter(Identifier).validate_python(delivery_id)
            presented = digest_webhook_secret(secret)
        except WebhookError:
            raise
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise WebhookError("invalid_input", "Invalid webhook delivery") from None
        if not hmac.compare_digest(presented, record.definition.secret_digest):
            raise WebhookError("unauthenticated", "Webhook credentials are invalid")
        stamped = parse_delivery_timestamp(timestamp_raw)
        now = require_aware_utc(self.clock.now())
        skew = abs(now - stamped)
        if skew > timedelta(seconds=record.definition.replay_window_seconds):
            raise WebhookError("invalid_input", "Webhook replay window exceeded")
        return now

    def _canonical_input(self, input_json: str) -> str:
        try:
            if len(input_json.encode()) > 65536:
                raise ValueError
            inputs = TypeAdapter(dict[str, JsonValue]).validate_json(input_json)
            return json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValidationError, ValueError, UnicodeError, RecursionError, TypeError):
            raise WebhookError("invalid_input", "Invalid webhook payload") from None

    def _reserved_receipt(
        self, scope: Scope, definition: WebhookDefinition, delivery_id: str, input_json: str
    ) -> StartReceipt | None:
        canonical = self._canonical_input(input_json)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        try:
            return self.start_repository.lookup(
                StartRequest(
                    scope,
                    definition.project_id,
                    definition.workflow_id,
                    delivery_id,
                    canonical,
                    source="webhook",
                ),
                fingerprint,
            )
        except StartError as error:
            raise WebhookError(error.code, str(error)) from None

    def _record_started(
        self,
        scope: Scope,
        webhook_id: str,
        delivery_id: str,
        received_at: datetime,
        receipt: StartReceipt,
    ) -> WebhookDeliveryRecord:
        return self.repository.save_delivery(
            scope,
            WebhookDeliveryRecord(
                webhook_id=webhook_id,
                delivery_id=delivery_id,
                received_at=received_at,
                status="started",
                execution_id=receipt.execution_id,
            ),
        )


class WebhookAdmin(_WebhookBoundary):
    """Signed create/list/disable without requiring a start dispatcher."""


class WebhookService(_WebhookBoundary):
    """Trusted admin + sync deliver for offline acceptance."""

    def __init__(
        self,
        repository: SQLiteWebhookRepository,
        authorization: WebhookAuthorization,
        starts: StartService,
        start_repository: SQLiteStartRepository,
        clock: WebhookClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    def deliver(
        self,
        scope: Scope,
        webhook_id: str,
        *,
        secret: str,
        timestamp: str,
        delivery_id: str,
        input_json: str = "{}",
    ) -> StartReceipt:
        existing = self.repository.get(scope, webhook_id)
        if existing is None:
            raise WebhookError("not_found", "Webhook was not found")
        # Delivery auth is secret-based; still scope the definition's project for start.
        received_at = self._authenticate(existing, secret, timestamp, delivery_id)
        prior = self.repository.delivery(scope, webhook_id, delivery_id)
        if prior is not None and prior.status == "started" and prior.execution_id is not None:
            reserved = self._reserved_receipt(
                scope, existing.definition, delivery_id, self._canonical_input(input_json)
            )
            if reserved is not None:
                return reserved
        canonical = self._canonical_input(input_json)
        try:
            receipt = self.starts.start(
                StartRequest(
                    scope,
                    existing.definition.project_id,
                    existing.definition.workflow_id,
                    delivery_id,
                    canonical,
                    source="webhook",
                )
            )
        except StartError as error:
            if error.code == "unavailable":
                reserved = self._reserved_receipt(
                    scope, existing.definition, delivery_id, canonical
                )
                if reserved is not None:
                    self._record_started(scope, webhook_id, delivery_id, received_at, reserved)
                    return reserved
            raise WebhookError(error.code, str(error)) from None
        self._record_started(scope, webhook_id, delivery_id, received_at, receipt)
        return receipt


class AsyncWebhookService(_WebhookBoundary):
    """HTTP deliver through the async durable start path."""

    def __init__(
        self,
        repository: SQLiteWebhookRepository,
        authorization: WebhookAuthorization,
        starts: AsyncStartService,
        start_repository: SQLiteStartRepository,
        clock: WebhookClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    async def deliver(
        self,
        scope: Scope,
        webhook_id: str,
        *,
        secret: str,
        timestamp: str,
        delivery_id: str,
        input_json: str = "{}",
    ) -> StartReceipt:
        existing = self.repository.get(scope, webhook_id)
        if existing is None:
            raise WebhookError("not_found", "Webhook was not found")
        received_at = self._authenticate(existing, secret, timestamp, delivery_id)
        prior = self.repository.delivery(scope, webhook_id, delivery_id)
        if prior is not None and prior.status == "started" and prior.execution_id is not None:
            reserved = self._reserved_receipt(
                scope, existing.definition, delivery_id, self._canonical_input(input_json)
            )
            if reserved is not None:
                return reserved
        canonical = self._canonical_input(input_json)
        try:
            receipt = await self.starts.start(
                StartRequest(
                    scope,
                    existing.definition.project_id,
                    existing.definition.workflow_id,
                    delivery_id,
                    canonical,
                    source="webhook",
                )
            )
        except StartError as error:
            if error.code == "unavailable":
                reserved = self._reserved_receipt(
                    scope, existing.definition, delivery_id, canonical
                )
                if reserved is not None:
                    self._record_started(scope, webhook_id, delivery_id, received_at, reserved)
                    return reserved
            raise WebhookError(error.code, str(error)) from None
        self._record_started(scope, webhook_id, delivery_id, received_at, receipt)
        return receipt


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
