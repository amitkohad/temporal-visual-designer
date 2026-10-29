# Atomic debug reconnect increment

Branch: `codex/050-debug-atomic-reconnect`, based on main `e6476bd`
(merged PR #47).

## Implemented boundary

Add typed no-store signed Dev `GET /projects/{project_id}/debug-sessions/{session_id}/reconnect`.
One synchronous read-only exact-run query captures payload-free runtime snapshot,
active breakpoints and earliest/latest event sequence together. The response's
`next_after` is that same latest sequence. Consumers render this baseline and
resume events after its cursor; retention gaps require another baseline rather
than replaying old metadata over newer state.

Every read checks current owner/Dev permission, acknowledged dispatch, placement
and saved run binding. Validation checks proof-plan node identity, unique ordered
completed prefix and current position, closed state/revision, canonical
executable breakpoints and exact retained-tail window. Zero event windows require
starting/revision zero/empty completed state, and starting cannot have a nonzero
cursor. Malformed or inconsistent providers fail closed.

No separate snapshot/breakpoint/event queries or mutable source/catalog/readiness
requests are combined. Immutable metadata copies remain stable after progress.
The public response is capped at 32 KiB for up to 100 completed node IDs and
breakpoint IDs together; existing 16 KiB event/value budgets are unchanged.
Inputs, outputs, error messages, actor IDs and event payloads are absent.

See [ADR 0048](../../docs/decisions/0048-atomic-debug-reconnect.md) and
[walkthrough](../../docs/debug-reconnect.md). No event-ordering/history command,
database migration, plan/result or dependency changes.

## Parallel ownership and verification

Shared contracts preceded disjoint runtime/adapter, service/validation and signed
API/actual acceptance lanes. The coordinator owns contracts, docs, checklists and
final review/regression. Independent final reviews found no blocking issues
after tightening zero-window/phase consistency.

Focused final-source checks: 54 service tests passed (5.11 seconds), 24
runtime/transport tests passed (1.37 seconds), and 17 signed API tests passed
(15.47 seconds). Maximum-size metadata acceptance covers 100 completed node IDs
and 98 breakpoint IDs above 16 KiB but within the new 32 KiB bound.

Three actual runtime scenarios passed (13.65 seconds), covering post-cursor
progress, immutable baselines, restart/terminal reads, normal-run denial,
retention overflow and replay. The signed actual reconnect scenario was rerun
after the final defensive service guard and passed (8.92 seconds).

Full actual Temporal regression: **75 passed, 236 deselected** (366.72 seconds).
The full run began before the final zero-window service guard; the focused
signed actual rerun above verifies that final source. Ignored
`artifacts/test-results/reconnect-temporal.log` and `reconnect-temporal.json`
record exit code zero. Full default regression on final source: **2,130 passed,
one expected Linux TCP TIME_WAIT test skipped on Windows, 75 integration tests
deselected** (412.92 seconds).

Ruff lint and formatting (439 Python files), mypy (98 source files), package
source/wheel build and `git diff --check` passed on final source. No hosted CI or
browser acceptance was run.

## Remaining scope

React consumers, streaming, detailed attempt/failure diagnostics, dynamic
variables, parallel-frontier reconciliation and production-provider readiness
remain open. Atomic metadata handoff does not establish those broader scenarios
or browser acceptance.
