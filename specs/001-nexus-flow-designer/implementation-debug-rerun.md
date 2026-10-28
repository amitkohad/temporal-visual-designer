# Explicit debug rerun increment

Branch: `codex/045-debug-rerun`, based on main `59867dd` (merged PR #42).

## Implemented boundary

Add the signed Dev `POST /projects/{project_id}/debug-sessions/{session_id}/reruns`
endpoint with the existing full `DebugStart` request and `DebugSession` response.
The parent must be acknowledged, owned by the freshly authorized caller and
bound to the current project/placement. The workflow ID must match the parent;
the new request selects its source revision, inputs, configuration and breakpoints
explicitly. Parent private inputs are not inherited.

A new operation creates a fresh session/execution through existing compilation,
readiness and exact-run start dispatch. The original run is neither queried nor
controlled and remains on its frozen plan. The new child can execute separately.

The child intent and scoped parent association are stored atomically in the
local SQLite repository. Exact parent/body retries recover the same child.
Normal-start operations cannot be adopted as reruns; rerun operations cannot be
reused through normal start or another parent. An additive `debug_reruns` table
preserves existing session/command schemas and checksums. Recovery must preserve
the whole database: association checksums detect changed values, not arbitrary
row deletion or partial database restoration.

See [ADR 0043](../../docs/decisions/0043-explicit-debug-rerun.md) and
[walkthrough](../../docs/debug-rerun.md).

## Parallel ownership and verification

The coordinator froze contracts and disjoint ownership before core/repository,
signed API and actual Temporal acceptance lanes. The coordinator owns docs,
checklists and final review/regression.

New acceptance: 12 core tests and 16 signed API tests. Combined rerun/existing
session core tests: 26 passed (2.50 seconds); combined API tests: 27 passed.
Core tests include lost dispatch acknowledgement, retries without mutable
source/readiness, transaction rollback, legacy migration, corrupted association
checks, and cross-connection concurrent parent claims. The focused actual
Temporal scenario passed (7.82 seconds), verifying an edited revision starts
while the parent worker is stopped, child identity/recovery, separate completion
while the parent remains paused, fresh revocation and both histories replaying.

Full default regression: **1,758 passed, one expected Linux TCP TIME_WAIT test
skipped on Windows, 57 integration tests deselected** (279.61 seconds). Ruff lint
and formatting (397 Python files), mypy (93 source files), source/wheel build and
`git diff --check` passed. Independent final review found no blocking issues.
Full actual Temporal regression: **57 passed, 236 deselected** (336.33 seconds).
Ignored `artifacts/test-results/rerun-temporal.log` and `rerun-temporal.json`
record exit code zero. No hosted CI or browser acceptance was run.

## Remaining scope

This verifies backend rerun/version isolation only. React controls and canvas
presentation, mock connections, event reconciliation, broader runtime inspection,
expiry, managed-task/active-activity cancellation and full debugger acceptance
remain open. No production-provider or browser acceptance is inferred from
signed synthetic identity fixtures and local Temporal execution.
