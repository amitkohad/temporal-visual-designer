# Project history API increment

Branch: `codex/029-project-history-api`, based on main `80df277` (PR #26).

## Implemented boundary

Signed HTTP history, comparison and restore routes compose actual identity and SQLite project providers. Metadata-only history uses exclusive descending revision cursors, 1-100 rows per page and no snapshot-content reads. Comparison preserves JSON Pointer escaping and missing-versus-null values, excludes layout by default and aborts beyond 1,000 changes or 262,144 encoded change-object bytes. Restore appends with expected-head concurrency and fresh Dev write permission; immutable history, published releases, activation and running workflows are retained. See [ADR 0027](../../docs/decisions/0027-project-history-api.md) and [API contracts](../../docs/api.md).

No new dependency, database schema or migration. Existing internal history/comparison interfaces remain compatible.

## Parallel ownership

Domain lane owned project_versioning types/repository/service/canonical and test_history_pages.py. HTTP lane owned api.py/api_models.py and test_project_history.py. Independent acceptance owned test_project_history_acceptance.py and test_project_history_integration.py. Shared method and response contracts were frozen before implementation; coordinator owns documentation and verification.

## Verification

Focused project-versioning suite: 66 passed, including 27 new tests. HTTP implementation suite: 26 passed. Independent signed HTTP/SQLite acceptance: 11 passed. New actual Temporal acceptance: 1 passed, proving a restore while a workflow waits leaves old/new runs pinned to active release A, retains release B and allows completion/replay.

Default regression: 1,104 passed, 1 platform-specific Linux TCP TIME_WAIT test skipped on Windows, 36 integration tests deselected (199.46 seconds). The final additional independent HTTP validation case was added after collection; the final complete independent HTTP file was rerun separately: 11 passed (9.30 seconds).

Full actual Temporal runtime integration suite: 36 passed, 198 deselected (216.16 seconds), using `.venv/Scripts/python.exe -m pytest -m integration nexusflow/tests/runtime -q`.

Ruff check and format check passed; mypy passed for 74 source files; `uv build` produced wheel and source distribution. Reciprocal independent domain and HTTP reviews reported no actionable defects.

## Remaining

F02-T006c is checked at this API/provider boundary. F02-T004 and full F02-T006 remain open for React project/history/compare/restore screens and complete conformance. Production Entra providers, multi-node production storage, package download/import and browser acceptance are not established by this increment. No hosted CI result is claimed.
