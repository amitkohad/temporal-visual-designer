# Debug wait cancellation increment

Branch: `codex/044-debug-wait-cancellation`, based on main `60c3e26` (merged PR #41).

## Implemented boundary

Extend the existing cancellation command to active timer waits and undecided
unmanaged approval waits in debug Dev runs. Preserve owner, exact-run, current
node/revision, durable receipt, saved plan and persistence contracts.

Acceptance records `cancelling` and a workflow-local cancellation flag. A wait
that finishes before cancellation delivery remains blocked before step
completion and downstream dispatch. The original waiting node remains visible.
The unchanged adapter requests exact-run Temporal cancellation after durable
acceptance and saves local receipts after RPC acknowledgement; exact retries
recover update outcomes and repeat delivery when needed.

Newly eligible wait cancellation uses the `debug-wait-cancellation-v1` Temporal
patch marker. Replay without that marker preserves previous rejection of wait
cancellation. Because the SDK caches this decision for the run, an execution
containing a pre-patch rejected wait-cancel conservatively retains that behavior
after worker migration. New executions use the extended behavior; paused
cancellation is unchanged.

See [ADR 0042](../../docs/decisions/0042-debug-wait-cancellation.md). This extends
the prior paused-only subset without a new API or database migration.

## Parallel ownership and verification

Contracts and ownership were frozen before parallel runtime/unit, actual
Temporal, and signed API/independent review lanes. The coordinator owns docs,
checklists, final review and regression. Independent final review found no
blocking issues.

New isolated acceptance: 31 runtime unit cases plus eight signed API cases.
Combined new/existing cancellation units: 38 passed (0.75 seconds). Signed new
API suite: eight passed (7.16 seconds). Four new actual Temporal cases verify
timer/approval cancellation, blocked timer expiry, unchanged managed-task guards,
and pre-patch failed-update live migration/replay. The final focused actual suite
passed all four (7.01 seconds).

Full default regression: **1,730 passed, one expected Linux TCP TIME_WAIT test
skipped on Windows, 56 integration tests deselected** (284.24 seconds). Ruff lint
and formatting (391 Python files), mypy (93 source files), package source/wheel
build and `git diff --check` passed. Full actual Temporal regression: **56 passed,
236 deselected** (331.20 seconds). Ignored evidence files
`artifacts/test-results/wait-cancel-temporal.log` and `wait-cancel-temporal.json`
record exit code zero. No hosted CI or browser acceptance was run.

## Remaining scope

Managed task creation/wait cancellation requires task lifecycle cleanup and
remains rejected. Running activity cancellation, expiry, background delivery
recovery, general parallel cancellation policy and React UI remain open. Full
DB-A6 and broader debugger parents remain unchecked; local proof-runtime and
signed fixture tests do not establish production-provider or browser acceptance.
