# Paused breakpoint controls increment

Branch: `codex/048-debug-breakpoint-controls`, based on main `b9e11fe`
(merged PR #45).

## Implemented boundary

Add signed GET/POST
`/projects/{project_id}/debug-sessions/{session_id}/breakpoints` for the original
owner in Dev. GET returns active targets and runtime revision. POST replaces the
whole set with canonical unique executable node IDs using current paused node,
expected revision and command ID. Empty replacement clears it.

Runtime maintains an overlay initialized from the immutable plan. Changing the
overlay never releases a permit or rewrites the source/start snapshot; current
pause remains even when removed from the set. Changes affect future boundaries
and do not rewind completed steps or interrupt active waits/work. Normal
executions, non-Dev and non-paused replacement are rejected.

Both command kinds share the existing debug command table, ID namespace and
1,000-command budget. The stored request union distinguishes `action` from
`breakpoints`, retaining old request JSON and receipts without a migration.
Exact successful retries bypass runtime after fresh authorization. Pending
identities remain bound to their original kind/body; remote updates use a stable
full-command identity for acknowledgement recovery on the saved exact run.

Queries return only metadata, target IDs and revision. Existing payload-free
status and plan/result formats are unchanged. Old histories use the same initial
breakpoints and emit no additional commands; new replacements replay through
their additive update handler.

See [ADR 0046](../../docs/decisions/0046-paused-breakpoint-controls.md) and
[walkthrough](../../docs/debug-breakpoints.md).

## Parallel ownership and verification

Shared contracts were frozen before service/repository, runtime/adapter and
signed API/actual acceptance lanes. The coordinator owns shared types, docs,
checklists and final review/regression. Independent final core/ledger review
found no blocking issues.

Focused checks: 28 new service/repository tests and 14 existing session tests
passed together (42, 8.90 seconds). New transport/workflow boundary checks:
27 passed (2.14 seconds). Signed API tests: 18 passed (32.64 seconds). Combined
actual runtime and signed API scenarios: four passed (19.69 seconds), including
replacement/clear, unchanged current gate, continue to changed targets,
worker/database restart, receipt retry and replay.

Ruff lint and formatting (422 Python files), mypy (96 source files), package
source/wheel build and `git diff --check` passed. Full actual Temporal regression:
**65 passed, 236 deselected** (470.85 seconds). Ignored
`artifacts/test-results/breakpoint-temporal.log` and `breakpoint-temporal.json`
record exit code zero. Full default regression: **1,948 passed, one expected
Linux TCP TIME_WAIT test skipped on Windows, 65 integration tests deselected**
(637.81 seconds).
No hosted CI or browser acceptance was run.

## Remaining scope

Changes during running activities, active timer/human waits, parallel frontier
semantics, event reconciliation and React breakpoint controls remain open. Local
signed fixtures and actual Temporal acceptance do not establish production
identity/provider or browser acceptance.
