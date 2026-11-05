# Persistent fixture Temporal worker increment

Branch: `codex/062-persistent-mock-worker`, based on main `bb5d2f2` (PR #59).

## Frozen interfaces and parallel plan

Extend `SQLiteHTTPMockScenarios` with keyword `read_only: bool = False`.
Read-only mode requires an existing file, opens SQLite `mode=ro`, validates the
effective committed schema and never executes schema creation, migrations or
journal-mode changes. Writes fail with private storage `unavailable` errors.
Existing write/admin defaults remain unchanged. Schema checking is shared through
`nexusflow.modules.connectors.mock_schema.validate_http_mock_schema(connection)`,
which raises a fixed `ValueError` on invalid schema and returns `None` on success.
It validates the same exact seven columns/types/not-null/composite primary key as
the local profile, including canonical physical column order required by existing
writable inserts. Normal SQLite locking sidecars may be managed.

`nexusflow.apps.persistent_http_mocks.open_persistent_http_mock_activities` is a
synchronous context manager with positional database, worker_authorization,
scope, namespace and task_queue arguments, plus required keyword `dev_scopes`
and optional `timeout_seconds=30`. It yields `HTTPJSONMockActivities`, composed
from the existing explicit Dev policy, actual read-only SQLite fixture store,
mock executor and HTTP JSON adapter. It has no live executor or network fallback.
Configuration failures are fixed private `ValueError`s; storage failures retain
private storage errors. Exceptions from the caller's context body propagate.

Hosts explicitly register `activities.dispatch` on a worker and must stop that
worker before exiting the context. The factory does not start workers, grant
permission, create fixtures or enable default launcher/publication behavior.
Trusted host configuration declares eligible Dev scopes; naming alone grants
nothing. Existing fresh worker permission and pin/context checks still apply.

1. [P] Storage lane owns shared schema checker, read-only store and storage tests.
2. [P] Composition lane owns explicit factory and lifecycle/configuration tests.
3. [P] Independent acceptance lane owns signed fixture creation to actual Temporal
   execution, worker/store restart, metadata privacy, authorization and replay.
4. Coordinator owns local-profile schema reuse, docs, verified checkboxes and
   final regressions. No lane edits another lane's files or syncs dependencies.

## Verification

Focused verification: **28 read-only storage tests passed**, with three real
symlink cases skipped for Windows host permissions (1.13 seconds). **37 factory
tests passed** (2.25 seconds). Existing repository/listing cases passed, and the
local profile regression passed **33 cases** with its four existing permission
skips (38.42 seconds) after adopting the shared checker.

Independent acceptance passed **seven real Temporal tests** (25.94 seconds):
signed API fixture creation, admin close/reopen, read-only worker/store restart,
debug pause and replay, private response omission from history, retry/new-run
identity, later admin revision visibility and non-retryable pin/live/permission
denials. Worker grants are synthetic; no deployed Entra or default publication
acceptance is inferred.

Cross-review found a physical-column-order compatibility gap in the shared schema
checker. It now rejects reordered schemas before use; an added regression passed.
Full regressions were restarted against the corrected frozen source. Ruff
lint/format, configured mypy (130 source files), lock verification and source/wheel
build passed, with wheel parity for all four final worker/storage/profile modules.
The full final-source real Temporal runtime suite passed **91 tests**, with 236
non-integration runtime tests deselected (616.64 seconds). Final independent
review found no blockers after the schema correction. Full final-source default
regression passed **3,700 tests**, with eight skips (seven real-symlink cases
requiring host permission and one Windows TCP TIME_WAIT exclusion) and 91
integration tests deselected (699.28 seconds). Symlink acceptance remains
explicitly unchecked in the feature task list. Final diff checks passed.

## Remaining scope

Direct compiled-plan execution is not executable built-in publication, project
export/import activation or default app workflow start. Browser UI, live effects,
production authorization/storage and immediate cancellation without heartbeats
remain separate concerns.
