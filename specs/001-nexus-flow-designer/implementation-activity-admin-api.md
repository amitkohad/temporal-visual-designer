# Activity administration API increment

Branch: `codex/030-activity-admin-api`, based on merged main `e47400a` (PR #27).

## Implemented boundary

Six signed HTTP routes compose the existing identity, intake and catalog services with actual local SQLite stores: private raw ZIP intake, import metadata, original archive download, publication, pinned catalog metadata and lifecycle transitions. See [API contracts](../../docs/api.md) and [ADR 0028](../../docs/decisions/0028-activity-admin-api.md).

Uploads preserve existing non-executing validation, logical digest/idempotency and immutable first-archive semantics. The default HTTP limit is 6,000,000 bytes; the domain archive ceiling remains 8 MiB. ZIP validation/storage runs outside the event loop. Private metadata/download requires fresh `activity.import`; publishing and lifecycle require `activity.publish`. Responses use strict typed contracts, sanitized errors and no-store/correlation headers, including binary downloads. Caller-supplied actor, scope and capabilities are rejected. Publication requires the configured capability provider and leaves imports private on failure.

Pinned metadata lookup intentionally includes withdrawn state; new-use domain resolution still rejects withdrawn activities. Lifecycle generation checks and idempotent same-target retries retain the existing domain contract. The API exposes neither deletion nor caller-controlled retention references. No role, domain schema, dependency or migration changes are required.

## Parallel ownership and verification

The HTTP implementation lane owned `apps/api.py`, `api_models.py`, `api_backend.py` and `tests/api/test_activity_admin.py`. Independent acceptance owned `test_activity_admin_acceptance.py`; security owned `test_activity_admin_security.py`. Route, permission, body/response and retry contracts were frozen before parallel work. The coordinator owns documentation and final verification.

The complete API suite passed: 122 tests (43.97 seconds), including 8 new implementation tests, 8 independent acceptance tests and 11 independent security tests. These tests use signed tokens and actual SQLite repositories. Declared and chunked upload limits are exercised through ASGI, malicious archive validation leaves no stored import, and source side effects never execute during import/publication/download. Capability failures use explicit provider fixtures and do not establish a deployed worker.

Two independent read-only implementation reviews reported no actionable defects. Ruff check and format check passed; mypy passed for 74 source files. `uv build` produced the source distribution and wheel. The full default suite passed: 1,132 passed, one Linux-only TCP TIME_WAIT test skipped on Windows, and 36 integration tests deselected (139.57 seconds). The full actual Temporal runtime regression passed: 36 passed, 198 deselected (183.64 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`.

## Remaining scope

F04-T006c and NF-T008c are checked at this bounded HTTP/provider boundary. Full F04-T006, Admin/Designer React screens, live Entra/provider acceptance, isolated Python execution, production storage and automatic transactional retention orchestration remain open. Whole-project package transfer and range/stream downloads are not implemented here. The synthetic local launcher does not grant publisher rights or approve custom worker capabilities. No hosted CI or browser acceptance is claimed.
