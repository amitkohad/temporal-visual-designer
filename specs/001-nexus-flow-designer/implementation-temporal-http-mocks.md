# Temporal HTTP mock dispatch increment

Branch: `codex/057-temporal-http-mocks`, based on main `abe5ce6` (PR #54).

## Bounded implementation and parallel plan

Run an HTTP JSON mock step through the existing proof workflow on an explicitly
configured Temporal worker. Freeze trusted context/prepared-call/adapter/error
contracts before parallel edits. Do not modify workflow serialization/replay,
enable live HTTP or change default host/publication behavior.

1. Coordinator owns contracts, decisions, guides, checklist evidence and checks.
2. [P] Pure validation lane owns actual-context/placement/release/reference/input
   checks and stable run-bound operation identity tests.
3. [P] Worker lane owns SDK activity registration, mock-only adapter invocation,
   status-only results and private deadline/cancellation/application-error tests.
4. [P] Independent acceptance owns real Temporal workflows, actual mock component
   composition, retry identity, debug/restart and replay tests.
5. Verify focused, default and real Temporal evidence before closing bounded
   subtasks. Parent feature acceptance remains open for publication/live/UI gaps.

See [ADR 0055](../../docs/decisions/0055-temporal-http-mocks.md) and the
[worker guide](../../docs/temporal-http-mocks.md).

## Verification

Independent cross-review found no blockers. Focused unit verification: **121
passed** (7.76 seconds): 66 pure context/input/operation identity and 55 worker
SDK/result/deadline/cancellation cases. Real Temporal acceptance: **six passed**
(14.39 seconds) using the installed CLI and actual mock components. It verifies
debug pause across worker restart, replay, stable identity across retry attempts,
distinct identity for a new run, private non-retryable live/reference/revocation
denials, and workflow cancellation with bounded lookup quiescence. No heartbeat
or immediate worker interruption guarantee is inferred.

Ruff lint/format (510 Python files) and configured mypy (121 source files) passed.
Full default regression: **3253 passed, one expected Windows skip, 81 integration
tests deselected** (428.24 seconds). Full real Temporal runtime regression:
**81 passed, 236 deselected** (422.07 seconds). Package build, all five schema
exports, lock verification and wheel/source parity for the three new modules
also passed. Independent final review found no actionable blockers. No dependencies
or proof workflow format changed.

## Remaining scope

Direct compiled-plan integration is not project publication or app deployment.
Built-in executable artifacts, activation/package capability handling, default
launcher/API/catalog/palette registration, general bindings, live HTTP effect
recovery, secrets and browser/cloud/production identity acceptance remain open.
