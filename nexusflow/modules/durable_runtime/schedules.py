"""Schedule persistence and due-occurrence fire into durable starts (ADR 0069)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from zoneinfo import ZoneInfo

from pydantic import JsonValue, TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.durable_runtime.schedule_types import (
    OverlapPolicy,
    ScheduleAuthorization,
    ScheduleClock,
    ScheduleDefinition,
    ScheduleError,
    ScheduleOccurrenceRecord,
    ScheduleRecord,
    occurrence_idempotency_key,
    require_aware_utc,
    validate_iana_timezone,
)
from nexusflow.modules.durable_runtime.starts import (
    AsyncStartService,
    SQLiteStartRepository,
    StartError,
    StartReceipt,
    StartRequest,
    StartService,
)

_FIELD = re.compile(r"^(?:\*(?:/\d+)?|\d+(?:-\d+)?(?:/\d+)?(?:,\d+(?:-\d+)?(?:/\d+)?)*)$")
_MAX_CATCH_UP = 64


def _expand(field: str, minimum: int, maximum: int) -> frozenset[int]:
    if not _FIELD.match(field):
        raise ScheduleError("invalid_input", "Cron expression is invalid")
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        body = part
        if "/" in part:
            body, raw_step = part.split("/", 1)
            if not raw_step.isdigit() or int(raw_step) < 1:
                raise ScheduleError("invalid_input", "Cron expression is invalid")
            step = int(raw_step)
        if body == "*":
            start, end = minimum, maximum
        elif "-" in body:
            raw_start, raw_end = body.split("-", 1)
            if not raw_start.isdigit() or not raw_end.isdigit():
                raise ScheduleError("invalid_input", "Cron expression is invalid")
            start, end = int(raw_start), int(raw_end)
        else:
            if not body.isdigit():
                raise ScheduleError("invalid_input", "Cron expression is invalid")
            start = end = int(body)
        if start > end or start < minimum or end > maximum:
            raise ScheduleError("invalid_input", "Cron expression is invalid")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        fields = expression.strip().split()
        if len(fields) != 5:
            raise ScheduleError("invalid_input", "Cron expression must have five fields")
        minutes = _expand(fields[0], 0, 59)
        hours = _expand(fields[1], 0, 23)
        days = _expand(fields[2], 1, 31)
        months = _expand(fields[3], 1, 12)
        # Cron Sunday may be 0 or 7; normalize both into the match set.
        dow = set(_expand(fields[4], 0, 7))
        if 7 in dow:
            dow.add(0)
        return cls(minutes, hours, frozenset(days), frozenset(months), frozenset(dow))

    def matches(self, local: datetime) -> bool:
        # Crontab weekday: 0/7 = Sunday … 6 = Saturday (Python weekday Mon=0).
        weekday = (local.weekday() + 1) % 7
        return (
            local.minute in self.minutes
            and local.hour in self.hours
            and local.day in self.days_of_month
            and local.month in self.months
            and weekday in self.days_of_week
        )


def iter_occurrences(
    cron: CronExpression, timezone: str, start_exclusive: datetime, end_inclusive: datetime
) -> list[datetime]:
    """Whole-minute civil occurrences in timezone, returned as aware UTC instants."""
    zone = ZoneInfo(timezone)
    cursor = require_aware_utc(start_exclusive).astimezone(zone)
    end_local = require_aware_utc(end_inclusive).astimezone(zone)
    # Advance to the next whole minute after start_exclusive.
    cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)
    found: list[datetime] = []
    # Guard against pathological dense cron + long catch-up windows.
    limit = _MAX_CATCH_UP
    scanned = 0
    max_scan = max(60 * 24 * 8, limit * 60)
    while cursor <= end_local and len(found) < limit and scanned < max_scan:
        scanned += 1
        if cron.matches(cursor):
            found.append(cursor.astimezone(UTC).replace(microsecond=0))
        cursor += timedelta(minutes=1)
        # Skip impossible day-of-month quickly when month ends early.
        last = monthrange(cursor.year, cursor.month)[1]
        if cursor.day > last:
            cursor = cursor.replace(day=1, hour=0, minute=0) + timedelta(days=last)
    return found


class SQLiteScheduleRepository:
    def __init__(self, database: str | Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(str(database), check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS runtime_schedules (
            org TEXT NOT NULL, env TEXT NOT NULL, schedule TEXT NOT NULL,
            project TEXT NOT NULL, workflow TEXT NOT NULL,
            cron TEXT NOT NULL, timezone TEXT NOT NULL, overlap TEXT NOT NULL,
            input_json TEXT NOT NULL, enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL, disabled_at TEXT,
            cursor_at TEXT NOT NULL,
            PRIMARY KEY (org, env, schedule))"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS runtime_schedule_occurrences (
            org TEXT NOT NULL, env TEXT NOT NULL, schedule TEXT NOT NULL,
            occurrence_at TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL, execution TEXT,
            PRIMARY KEY (org, env, schedule, occurrence_at),
            UNIQUE (org, env, idempotency_key))"""
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _row(self, row: tuple[object, ...]) -> ScheduleRecord:
        enabled = bool(row[9])
        if row[7] not in {"allow", "skip"}:
            raise ScheduleError("invalid_input", "Stored overlap policy is invalid")
        overlap: OverlapPolicy = row[7]  # type: ignore[assignment]
        definition = ScheduleDefinition(
            schedule_id=str(row[2]),
            scope=Scope(organization_id=str(row[0]), environment_id=str(row[1])),
            project_id=str(row[3]),
            workflow_id=str(row[4]),
            cron=str(row[5]),
            timezone=str(row[6]),
            overlap_policy=overlap,
            input_json=str(row[8]),
            enabled=enabled,
        )
        return ScheduleRecord(
            definition=definition,
            created_at=datetime.fromisoformat(str(row[10])),
            disabled_at=None if row[11] is None else datetime.fromisoformat(str(row[11])),
        )

    def get(self, scope: Scope, schedule_id: str) -> ScheduleRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT org,env,schedule,project,workflow,cron,timezone,overlap,input_json,
                enabled,created_at,disabled_at,cursor_at FROM runtime_schedules
                WHERE org=? AND env=? AND schedule=?""",
                (scope.organization_id, scope.environment_id, schedule_id),
            ).fetchone()
        return None if row is None else self._row(row)

    def cursor_at(self, scope: Scope, schedule_id: str) -> datetime:
        with self._lock:
            row = self._db.execute(
                "SELECT cursor_at FROM runtime_schedules WHERE org=? AND env=? AND schedule=?",
                (scope.organization_id, scope.environment_id, schedule_id),
            ).fetchone()
        if row is None:
            raise ScheduleError("not_found", "Schedule was not found")
        return datetime.fromisoformat(row[0])

    def list_records(
        self, scope: Scope, project_id: str | None = None
    ) -> tuple[ScheduleRecord, ...]:
        query = """SELECT org,env,schedule,project,workflow,cron,timezone,overlap,input_json,
            enabled,created_at,disabled_at,cursor_at FROM runtime_schedules
            WHERE org=? AND env=?"""
        params: list[object] = [scope.organization_id, scope.environment_id]
        if project_id is not None:
            query += " AND project=?"
            params.append(project_id)
        query += " ORDER BY schedule"
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return tuple(self._row(row) for row in rows)

    def create(self, record: ScheduleRecord, cursor_at: datetime) -> ScheduleRecord:
        definition = record.definition
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        definition.scope.organization_id,
                        definition.scope.environment_id,
                        definition.schedule_id,
                        definition.project_id,
                        definition.workflow_id,
                        definition.cron,
                        definition.timezone,
                        definition.overlap_policy,
                        definition.input_json,
                        1 if definition.enabled else 0,
                        require_aware_utc(record.created_at).isoformat(),
                        None
                        if record.disabled_at is None
                        else require_aware_utc(record.disabled_at).isoformat(),
                        require_aware_utc(cursor_at).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise ScheduleError("conflict", "Schedule already exists") from None
        saved = self.get(definition.scope, definition.schedule_id)
        assert saved is not None
        return saved

    def disable(self, scope: Scope, schedule_id: str, at: datetime) -> ScheduleRecord:
        with self._lock, self._db:
            updated = self._db.execute(
                """UPDATE runtime_schedules SET enabled=0, disabled_at=?
                WHERE org=? AND env=? AND schedule=? AND enabled=1""",
                (
                    require_aware_utc(at).isoformat(),
                    scope.organization_id,
                    scope.environment_id,
                    schedule_id,
                ),
            ).rowcount
        if updated == 0:
            existing = self.get(scope, schedule_id)
            if existing is None:
                raise ScheduleError("not_found", "Schedule was not found")
            return existing
        saved = self.get(scope, schedule_id)
        assert saved is not None
        return saved

    def advance_cursor(self, scope: Scope, schedule_id: str, cursor_at: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                """UPDATE runtime_schedules SET cursor_at=?
                WHERE org=? AND env=? AND schedule=?""",
                (
                    require_aware_utc(cursor_at).isoformat(),
                    scope.organization_id,
                    scope.environment_id,
                    schedule_id,
                ),
            )

    def occurrence(
        self, scope: Scope, schedule_id: str, occurrence_at: datetime
    ) -> ScheduleOccurrenceRecord | None:
        key = require_aware_utc(occurrence_at).replace(microsecond=0).isoformat()
        with self._lock:
            row = self._db.execute(
                """SELECT schedule,occurrence_at,idempotency_key,status,execution
                FROM runtime_schedule_occurrences
                WHERE org=? AND env=? AND schedule=? AND occurrence_at=?""",
                (scope.organization_id, scope.environment_id, schedule_id, key),
            ).fetchone()
        if row is None:
            return None
        return ScheduleOccurrenceRecord(
            schedule_id=row[0],
            occurrence_at=datetime.fromisoformat(row[1]),
            idempotency_key=row[2],
            status=row[3],
            execution_id=row[4],
        )

    def latest_started(self, scope: Scope, schedule_id: str) -> ScheduleOccurrenceRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT schedule,occurrence_at,idempotency_key,status,execution
                FROM runtime_schedule_occurrences
                WHERE org=? AND env=? AND schedule=? AND status='started'
                ORDER BY occurrence_at DESC LIMIT 1""",
                (scope.organization_id, scope.environment_id, schedule_id),
            ).fetchone()
        if row is None:
            return None
        return ScheduleOccurrenceRecord(
            schedule_id=row[0],
            occurrence_at=datetime.fromisoformat(row[1]),
            idempotency_key=row[2],
            status=row[3],
            execution_id=row[4],
        )

    def save_occurrence(
        self, scope: Scope, record: ScheduleOccurrenceRecord
    ) -> ScheduleOccurrenceRecord:
        occurrence = require_aware_utc(record.occurrence_at).replace(microsecond=0)
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_schedule_occurrences
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        scope.organization_id,
                        scope.environment_id,
                        record.schedule_id,
                        occurrence.isoformat(),
                        record.idempotency_key,
                        record.status,
                        record.execution_id,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.occurrence(scope, record.schedule_id, occurrence)
                if existing is None:
                    raise ScheduleError("conflict", "Schedule occurrence conflicts") from None
                return existing
        saved = self.occurrence(scope, record.schedule_id, occurrence)
        assert saved is not None
        return saved


class _ScheduleBoundary:
    def __init__(
        self,
        repository: SQLiteScheduleRepository,
        authorization: ScheduleAuthorization,
        start_repository: SQLiteStartRepository,
        clock: ScheduleClock,
    ) -> None:
        self.repository = repository
        self.authorization = authorization
        self.start_repository = start_repository
        self.clock = clock

    def create(self, definition: ScheduleDefinition) -> ScheduleRecord:
        self.authorization.authorize(definition.scope, definition.project_id)
        try:
            for value in (
                definition.schedule_id,
                definition.project_id,
                definition.workflow_id,
            ):
                TypeAdapter(Identifier).validate_python(value)
            CronExpression.parse(definition.cron)
            validate_iana_timezone(definition.timezone)
            TypeAdapter(dict[str, JsonValue]).validate_json(definition.input_json)
            # Leave room for `.{YYYYMMDDTHHMMSSZ}` on occurrence keys.
            if len(definition.schedule_id) > 111:
                raise ValueError
            occurrence_idempotency_key(
                definition.schedule_id, self.clock.now().replace(microsecond=0)
            )
        except ScheduleError:
            raise
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise ScheduleError("invalid_input", "Invalid schedule definition") from None
        now = require_aware_utc(self.clock.now())
        # Cursor starts at create time so history before create is not catch-up fired.
        return self.repository.create(
            ScheduleRecord(definition=definition, created_at=now, disabled_at=None),
            cursor_at=now,
        )

    def list_schedules(self, scope: Scope, project_id: str) -> tuple[ScheduleRecord, ...]:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(project_id)
        return self.repository.list_records(scope, project_id)

    def disable(self, scope: Scope, project_id: str, schedule_id: str) -> ScheduleRecord:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(schedule_id)
        TypeAdapter(Identifier).validate_python(project_id)
        existing = self.repository.get(scope, schedule_id)
        if existing is None:
            raise ScheduleError("not_found", "Schedule was not found")
        if existing.definition.project_id != project_id:
            raise ScheduleError("not_found", "Schedule was not found")
        return self.repository.disable(scope, schedule_id, self.clock.now())

    def _prior_undelivered(self, scope: Scope, schedule_id: str, project_id: str) -> bool:
        prior = self.repository.latest_started(scope, schedule_id)
        if prior is None or prior.execution_id is None:
            return False
        receipt = self.start_repository.execution(scope, project_id, prior.execution_id)
        return receipt is not None and not receipt.dispatched

    def _prepare_fire(
        self, scope: Scope, limit: int
    ) -> tuple[datetime, list[tuple[ScheduleDefinition, list[datetime]]]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScheduleError("invalid_input", "Fire limit must be between 1 and 100")
        now = require_aware_utc(self.clock.now())
        planned: list[tuple[ScheduleDefinition, list[datetime]]] = []
        for record in self.repository.list_records(scope):
            definition = record.definition
            if not definition.enabled:
                continue
            self.authorization.authorize(scope, definition.project_id)
            cron = CronExpression.parse(definition.cron)
            cursor = self.repository.cursor_at(scope, definition.schedule_id)
            due = iter_occurrences(cron, definition.timezone, cursor, now)
            planned.append((definition, due))
        return now, planned

    def _skip_or_none(
        self, scope: Scope, definition: ScheduleDefinition, occurrence: datetime, key: str
    ) -> ScheduleOccurrenceRecord | None:
        if definition.overlap_policy == "skip" and self._prior_undelivered(
            scope, definition.schedule_id, definition.project_id
        ):
            return self.repository.save_occurrence(
                scope,
                ScheduleOccurrenceRecord(
                    schedule_id=definition.schedule_id,
                    occurrence_at=occurrence,
                    idempotency_key=key,
                    status="skipped",
                ),
            )
        return None

    def _record_started(
        self,
        scope: Scope,
        definition: ScheduleDefinition,
        occurrence: datetime,
        key: str,
        receipt: StartReceipt,
    ) -> None:
        self.repository.save_occurrence(
            scope,
            ScheduleOccurrenceRecord(
                schedule_id=definition.schedule_id,
                occurrence_at=occurrence,
                idempotency_key=key,
                status="started",
                execution_id=receipt.execution_id,
            ),
        )

    def _reserved_receipt(
        self, scope: Scope, definition: ScheduleDefinition, key: str
    ) -> StartReceipt | None:
        inputs = TypeAdapter(dict[str, JsonValue]).validate_json(definition.input_json)
        canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        return self.start_repository.lookup(
            StartRequest(
                scope,
                definition.project_id,
                definition.workflow_id,
                key,
                definition.input_json,
                source="schedule",
            ),
            fingerprint,
        )

    def _advance(
        self,
        scope: Scope,
        schedule_id: str,
        cursor: datetime,
        advanced: datetime,
        due: list[datetime],
        now: datetime,
    ) -> None:
        if due:
            self.repository.advance_cursor(scope, schedule_id, max(advanced, cursor))
        elif now > cursor:
            self.repository.advance_cursor(scope, schedule_id, now)


class ScheduleAdmin(_ScheduleBoundary):
    """Signed create/list/disable without requiring a start dispatcher."""


class ScheduleService(_ScheduleBoundary):
    """Trusted create/list/disable and sync due-fire for offline acceptance."""

    def __init__(
        self,
        repository: SQLiteScheduleRepository,
        authorization: ScheduleAuthorization,
        starts: StartService,
        start_repository: SQLiteStartRepository,
        clock: ScheduleClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    def fire_due(self, scope: Scope, *, limit: int = 25) -> int:
        now, planned = self._prepare_fire(scope, limit)
        fired = 0
        for definition, due in planned:
            if fired >= limit:
                break
            cursor = self.repository.cursor_at(scope, definition.schedule_id)
            advanced = cursor
            for occurrence in due:
                if fired >= limit:
                    break
                existing = self.repository.occurrence(scope, definition.schedule_id, occurrence)
                if existing is not None:
                    advanced = occurrence
                    continue
                key = occurrence_idempotency_key(definition.schedule_id, occurrence)
                if self._skip_or_none(scope, definition, occurrence, key) is not None:
                    advanced = occurrence
                    continue
                try:
                    receipt = self.starts.start(
                        StartRequest(
                            scope,
                            definition.project_id,
                            definition.workflow_id,
                            key,
                            definition.input_json,
                            source="schedule",
                        )
                    )
                except StartError as error:
                    if error.code == "unavailable":
                        reserved = self._reserved_receipt(scope, definition, key)
                        if reserved is not None:
                            self._record_started(scope, definition, occurrence, key, reserved)
                        advanced = occurrence - timedelta(seconds=1)
                        break
                    raise ScheduleError(error.code, str(error)) from None
                self._record_started(scope, definition, occurrence, key, receipt)
                advanced = occurrence
                fired += 1
            self._advance(scope, definition.schedule_id, cursor, advanced, due, now)
        return fired


class AsyncScheduleService(_ScheduleBoundary):
    """HTTP/local due-fire through the async durable start path."""

    def __init__(
        self,
        repository: SQLiteScheduleRepository,
        authorization: ScheduleAuthorization,
        starts: AsyncStartService,
        start_repository: SQLiteStartRepository,
        clock: ScheduleClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    async def fire_due(self, scope: Scope, *, limit: int = 25) -> int:
        now, planned = self._prepare_fire(scope, limit)
        fired = 0
        for definition, due in planned:
            if fired >= limit:
                break
            cursor = self.repository.cursor_at(scope, definition.schedule_id)
            advanced = cursor
            for occurrence in due:
                if fired >= limit:
                    break
                existing = self.repository.occurrence(scope, definition.schedule_id, occurrence)
                if existing is not None:
                    advanced = occurrence
                    continue
                key = occurrence_idempotency_key(definition.schedule_id, occurrence)
                if self._skip_or_none(scope, definition, occurrence, key) is not None:
                    advanced = occurrence
                    continue
                try:
                    receipt = await self.starts.start(
                        StartRequest(
                            scope,
                            definition.project_id,
                            definition.workflow_id,
                            key,
                            definition.input_json,
                            source="schedule",
                        )
                    )
                except StartError as error:
                    if error.code == "unavailable":
                        reserved = self._reserved_receipt(scope, definition, key)
                        if reserved is not None:
                            self._record_started(scope, definition, occurrence, key, reserved)
                        advanced = occurrence - timedelta(seconds=1)
                        break
                    raise ScheduleError(error.code, str(error)) from None
                self._record_started(scope, definition, occurrence, key, receipt)
                advanced = occurrence
                fired += 1
            self._advance(scope, definition.schedule_id, cursor, advanced, due, now)
        return fired


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


async def run_schedule_poller(
    service: AsyncScheduleService, scope: Scope, *, poll_interval: float = 1.0
) -> None:
    """Owned poll loop; cancel the task to stop. Failures are retried after sleep."""
    if (
        isinstance(poll_interval, bool)
        or not isinstance(poll_interval, (int, float))
        or not 0.1 <= poll_interval <= 300
    ):
        raise ValueError("poll_interval must be between 0.1 and 300 seconds")
    while True:
        try:
            await service.fire_due(scope)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
