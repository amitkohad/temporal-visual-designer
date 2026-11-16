"""Form-start persistence and signed submit into durable starts (ADR 0071)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import JsonValue, TypeAdapter, ValidationError

from nexusflow.contracts.models import Identifier, Scope
from nexusflow.modules.designer_forms.form_definition import validate_form_definition
from nexusflow.modules.durable_runtime.form_start_types import (
    FormStartAuthorization,
    FormStartClock,
    FormStartDefinition,
    FormStartError,
    FormStartRecord,
    FormStartSubmissionRecord,
    require_aware_utc,
)
from nexusflow.modules.durable_runtime.starts import (
    AsyncStartService,
    SQLiteStartRepository,
    StartError,
    StartReceipt,
    StartRequest,
    StartService,
)
from nexusflow.modules.project_versioning.canonical import canonical
from nexusflow.modules.project_versioning.types import FormDefinition


def _pin_form_definition(form_definition_json: str) -> str:
    try:
        if len(form_definition_json.encode("utf-8")) > 1_048_576:
            raise ValueError
        raw = json.loads(form_definition_json)
        if type(raw) is not dict:
            raise ValueError
        definition = FormDefinition.model_validate(raw)
    except (ValidationError, ValueError, UnicodeError, TypeError, RecursionError):
        raise FormStartError("invalid_definition", "Invalid form definition") from None
    issues = validate_form_definition(definition)
    if issues:
        raise FormStartError("invalid_definition", "Unsupported form definition")
    pinned = canonical(definition.model_dump(mode="json"))
    if len(pinned.encode("utf-8")) > 1_048_576:
        raise FormStartError("invalid_definition", "Form definition exceeds the supported size")
    return pinned


def _validate_values(form_definition_json: str, values: dict[str, JsonValue]) -> str:
    definition = FormDefinition.model_validate_json(form_definition_json)
    schemas: list[dict[str, Any]] = [dict(definition.data_schema)]
    for raw in definition.validation_rules:
        if isinstance(raw, dict) and "data_schema" in raw and isinstance(raw["data_schema"], dict):
            schemas.append(dict(raw["data_schema"]))
    for schema in schemas:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(values),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            raise FormStartError("invalid_input", "Form start values failed schema validation")
    try:
        return json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise FormStartError("invalid_input", "Invalid form start values") from None


class SQLiteFormStartRepository:
    def __init__(self, path: Path) -> None:
        self._lock = RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS runtime_form_starts (
                org TEXT NOT NULL, env TEXT NOT NULL, form_start TEXT NOT NULL,
                project TEXT NOT NULL, workflow TEXT NOT NULL,
                form_definition TEXT NOT NULL, enabled INTEGER NOT NULL,
                created_at TEXT NOT NULL, disabled_at TEXT,
                PRIMARY KEY (org, env, form_start))"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS runtime_form_start_submissions (
                org TEXT NOT NULL, env TEXT NOT NULL, form_start TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, submitted_at TEXT NOT NULL,
                execution TEXT,
                PRIMARY KEY (org, env, form_start, idempotency_key))"""
            )

    def close(self) -> None:
        self._db.close()

    def create(self, record: FormStartRecord) -> FormStartRecord:
        definition = record.definition
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_form_starts
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        definition.scope.organization_id,
                        definition.scope.environment_id,
                        definition.form_start_id,
                        definition.project_id,
                        definition.workflow_id,
                        definition.form_definition_json,
                        1 if definition.enabled else 0,
                        require_aware_utc(record.created_at).isoformat(),
                        None
                        if record.disabled_at is None
                        else require_aware_utc(record.disabled_at).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError:
                raise FormStartError("conflict", "Form start already exists") from None
        saved = self.get(definition.scope, definition.form_start_id)
        assert saved is not None
        return saved

    def get(self, scope: Scope, form_start_id: str) -> FormStartRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT form_start,project,workflow,form_definition,enabled,created_at,disabled_at
                FROM runtime_form_starts WHERE org=? AND env=? AND form_start=?""",
                (scope.organization_id, scope.environment_id, form_start_id),
            ).fetchone()
        if row is None:
            return None
        return FormStartRecord(
            definition=FormStartDefinition(
                form_start_id=row[0],
                scope=scope,
                project_id=row[1],
                workflow_id=row[2],
                form_definition_json=row[3],
                enabled=bool(row[4]),
            ),
            created_at=datetime.fromisoformat(row[5]),
            disabled_at=None if row[6] is None else datetime.fromisoformat(row[6]),
        )

    def list_records(
        self, scope: Scope, project_id: str | None = None
    ) -> tuple[FormStartRecord, ...]:
        with self._lock:
            if project_id is None:
                rows = self._db.execute(
                    """SELECT form_start,project,workflow,form_definition,enabled,created_at,disabled_at
                    FROM runtime_form_starts WHERE org=? AND env=? ORDER BY form_start""",
                    (scope.organization_id, scope.environment_id),
                ).fetchall()
            else:
                rows = self._db.execute(
                    """SELECT form_start,project,workflow,form_definition,enabled,created_at,disabled_at
                    FROM runtime_form_starts
                    WHERE org=? AND env=? AND project=? ORDER BY form_start""",
                    (scope.organization_id, scope.environment_id, project_id),
                ).fetchall()
        return tuple(
            FormStartRecord(
                definition=FormStartDefinition(
                    form_start_id=row[0],
                    scope=scope,
                    project_id=row[1],
                    workflow_id=row[2],
                    form_definition_json=row[3],
                    enabled=bool(row[4]),
                ),
                created_at=datetime.fromisoformat(row[5]),
                disabled_at=None if row[6] is None else datetime.fromisoformat(row[6]),
            )
            for row in rows
        )

    def disable(self, scope: Scope, form_start_id: str, at: datetime) -> FormStartRecord:
        instant = require_aware_utc(at)
        with self._lock, self._db:
            existing = self.get(scope, form_start_id)
            if existing is None:
                raise FormStartError("not_found", "Form start was not found")
            if not existing.definition.enabled:
                return existing
            self._db.execute(
                """UPDATE runtime_form_starts SET enabled=0, disabled_at=?
                WHERE org=? AND env=? AND form_start=?""",
                (
                    instant.isoformat(),
                    scope.organization_id,
                    scope.environment_id,
                    form_start_id,
                ),
            )
        saved = self.get(scope, form_start_id)
        assert saved is not None
        return saved

    def submission(
        self, scope: Scope, form_start_id: str, idempotency_key: str
    ) -> FormStartSubmissionRecord | None:
        with self._lock:
            row = self._db.execute(
                """SELECT form_start,idempotency_key,submitted_at,execution
                FROM runtime_form_start_submissions
                WHERE org=? AND env=? AND form_start=? AND idempotency_key=?""",
                (scope.organization_id, scope.environment_id, form_start_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return FormStartSubmissionRecord(
            form_start_id=row[0],
            idempotency_key=row[1],
            submitted_at=datetime.fromisoformat(row[2]),
            execution_id=row[3],
        )

    def save_submission(
        self, scope: Scope, record: FormStartSubmissionRecord
    ) -> FormStartSubmissionRecord:
        submitted = require_aware_utc(record.submitted_at)
        with self._lock, self._db:
            try:
                self._db.execute(
                    """INSERT INTO runtime_form_start_submissions
                    VALUES (?,?,?,?,?,?)""",
                    (
                        scope.organization_id,
                        scope.environment_id,
                        record.form_start_id,
                        record.idempotency_key,
                        submitted.isoformat(),
                        record.execution_id,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.submission(scope, record.form_start_id, record.idempotency_key)
                if existing is None:
                    raise FormStartError("conflict", "Form start submission conflicts") from None
                return existing
        saved = self.submission(scope, record.form_start_id, record.idempotency_key)
        assert saved is not None
        return saved


class _FormStartBoundary:
    def __init__(
        self,
        repository: SQLiteFormStartRepository,
        authorization: FormStartAuthorization,
        start_repository: SQLiteStartRepository,
        clock: FormStartClock,
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
        form_start_id: str,
        workflow_id: str,
        form_definition_json: str,
    ) -> FormStartRecord:
        self.authorization.authorize(scope, project_id)
        try:
            for value in (form_start_id, project_id, workflow_id):
                TypeAdapter(Identifier).validate_python(value)
            pinned = _pin_form_definition(form_definition_json)
            definition = FormStartDefinition(
                form_start_id=form_start_id,
                scope=scope,
                project_id=project_id,
                workflow_id=workflow_id,
                form_definition_json=pinned,
                enabled=True,
            )
        except FormStartError:
            raise
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise FormStartError("invalid_input", "Invalid form start definition") from None
        now = require_aware_utc(self.clock.now())
        return self.repository.create(
            FormStartRecord(definition=definition, created_at=now, disabled_at=None)
        )

    def list_form_starts(self, scope: Scope, project_id: str) -> tuple[FormStartRecord, ...]:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(project_id)
        return self.repository.list_records(scope, project_id)

    def disable(self, scope: Scope, project_id: str, form_start_id: str) -> FormStartRecord:
        self.authorization.authorize(scope, project_id)
        TypeAdapter(Identifier).validate_python(form_start_id)
        TypeAdapter(Identifier).validate_python(project_id)
        existing = self.repository.get(scope, form_start_id)
        if existing is None:
            raise FormStartError("not_found", "Form start was not found")
        if existing.definition.project_id != project_id:
            raise FormStartError("not_found", "Form start was not found")
        return self.repository.disable(scope, form_start_id, self.clock.now())

    def _reserved_receipt(
        self,
        scope: Scope,
        definition: FormStartDefinition,
        idempotency_key: str,
        input_json: str,
    ) -> StartReceipt | None:
        fingerprint = hashlib.sha256(input_json.encode()).hexdigest()
        try:
            return self.start_repository.lookup(
                StartRequest(
                    scope,
                    definition.project_id,
                    definition.workflow_id,
                    idempotency_key,
                    input_json,
                    source="form",
                ),
                fingerprint,
            )
        except StartError as error:
            raise FormStartError(error.code, str(error)) from None

    def _record_started(
        self,
        scope: Scope,
        form_start_id: str,
        idempotency_key: str,
        submitted_at: datetime,
        receipt: StartReceipt,
    ) -> FormStartSubmissionRecord:
        return self.repository.save_submission(
            scope,
            FormStartSubmissionRecord(
                form_start_id=form_start_id,
                idempotency_key=idempotency_key,
                submitted_at=submitted_at,
                execution_id=receipt.execution_id,
            ),
        )


class FormStartAdmin(_FormStartBoundary):
    """Signed create/list/disable without requiring a start dispatcher."""


class FormStartService(_FormStartBoundary):
    """Trusted admin + sync submit for offline acceptance."""

    def __init__(
        self,
        repository: SQLiteFormStartRepository,
        authorization: FormStartAuthorization,
        starts: StartService,
        start_repository: SQLiteStartRepository,
        clock: FormStartClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    def submit(
        self,
        scope: Scope,
        project_id: str,
        form_start_id: str,
        *,
        idempotency_key: str,
        values: dict[str, JsonValue],
    ) -> StartReceipt:
        self.authorization.authorize(scope, project_id)
        try:
            TypeAdapter(Identifier).validate_python(form_start_id)
            TypeAdapter(Identifier).validate_python(idempotency_key)
            TypeAdapter(dict[str, JsonValue]).validate_python(values)
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise FormStartError("invalid_input", "Invalid form start submission") from None
        existing = self.repository.get(scope, form_start_id)
        if existing is None or existing.definition.project_id != project_id:
            raise FormStartError("not_found", "Form start was not found")
        if not existing.definition.enabled:
            raise FormStartError("not_found", "Form start was not found")
        input_json = _validate_values(existing.definition.form_definition_json, values)
        prior = self.repository.submission(scope, form_start_id, idempotency_key)
        if prior is not None and prior.execution_id is not None:
            reserved = self._reserved_receipt(
                scope, existing.definition, idempotency_key, input_json
            )
            if reserved is not None:
                return reserved
        submitted_at = require_aware_utc(self.clock.now())
        try:
            receipt = self.starts.start(
                StartRequest(
                    scope,
                    existing.definition.project_id,
                    existing.definition.workflow_id,
                    idempotency_key,
                    input_json,
                    source="form",
                )
            )
        except StartError as error:
            if error.code == "unavailable":
                reserved = self._reserved_receipt(
                    scope, existing.definition, idempotency_key, input_json
                )
                if reserved is not None:
                    self._record_started(
                        scope, form_start_id, idempotency_key, submitted_at, reserved
                    )
                    return reserved
            raise FormStartError(error.code, str(error)) from None
        self._record_started(scope, form_start_id, idempotency_key, submitted_at, receipt)
        return receipt


class AsyncFormStartService(_FormStartBoundary):
    """HTTP submit through the async durable start path."""

    def __init__(
        self,
        repository: SQLiteFormStartRepository,
        authorization: FormStartAuthorization,
        starts: AsyncStartService,
        start_repository: SQLiteStartRepository,
        clock: FormStartClock,
    ) -> None:
        super().__init__(repository, authorization, start_repository, clock)
        self.starts = starts

    async def submit(
        self,
        scope: Scope,
        project_id: str,
        form_start_id: str,
        *,
        idempotency_key: str,
        values: dict[str, JsonValue],
    ) -> StartReceipt:
        self.authorization.authorize(scope, project_id)
        try:
            TypeAdapter(Identifier).validate_python(form_start_id)
            TypeAdapter(Identifier).validate_python(idempotency_key)
            TypeAdapter(dict[str, JsonValue]).validate_python(values)
        except (ValidationError, ValueError, UnicodeError, TypeError):
            raise FormStartError("invalid_input", "Invalid form start submission") from None
        existing = self.repository.get(scope, form_start_id)
        if existing is None or existing.definition.project_id != project_id:
            raise FormStartError("not_found", "Form start was not found")
        if not existing.definition.enabled:
            raise FormStartError("not_found", "Form start was not found")
        input_json = _validate_values(existing.definition.form_definition_json, values)
        prior = self.repository.submission(scope, form_start_id, idempotency_key)
        if prior is not None and prior.execution_id is not None:
            reserved = self._reserved_receipt(
                scope, existing.definition, idempotency_key, input_json
            )
            if reserved is not None:
                return reserved
        submitted_at = require_aware_utc(self.clock.now())
        try:
            receipt = await self.starts.start(
                StartRequest(
                    scope,
                    existing.definition.project_id,
                    existing.definition.workflow_id,
                    idempotency_key,
                    input_json,
                    source="form",
                )
            )
        except StartError as error:
            if error.code == "unavailable":
                reserved = self._reserved_receipt(
                    scope, existing.definition, idempotency_key, input_json
                )
                if reserved is not None:
                    self._record_started(
                        scope, form_start_id, idempotency_key, submitted_at, reserved
                    )
                    return reserved
            raise FormStartError(error.code, str(error)) from None
        self._record_started(scope, form_start_id, idempotency_key, submitted_at, receipt)
        return receipt


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
