# Paused debug cancellation increment

Branch: `codex/043-debug-session-cancellation`, based on main `5bb8eae` (merged PR #40).

## Implemented boundary

Add `cancel` to the existing debug command action. Preserve the command fields,
permanent receipts, frozen plan, saved exact-run binding and local database
formats. Fresh owner/Dev authorization remains mandatory before every request,
including successful receipt retries. No publication or activation changes.

The sequential proof workflow accepts cancel only at the named paused boundary
and current revision. Acceptance records a durable receipt, marks `cancelling`
and leaves the permit blocked. The exact-run adapter requests Temporal
cancellation after obtaining that receipt. The workflow's existing cancellation
handler records `cancelled`; Temporal closes the execution as CANCELED. A local
receipt is saved after the cancellation RPC acknowledgement. A retry retrieves
the original update outcome and repeats the exact-run cancellation if needed.
Acknowledgement does not promise synchronous terminal state.

The SDK implementation was inspected locally: throwing a cancellation exception
without a server cancellation request does not establish a properly canceled
execution. The two-stage protocol therefore retains the blocked frontier until
Temporal delivers its actual cancellation request. A failed RPC can leave a
session `cancelling` until the caller retries; no recovery sweep is claimed.

See [ADR 0041](../../docs/decisions/0041-paused-debug-cancellation.md) and
[API walkthrough](../../docs/debug-session-cancellation.md).

## Parallel ownership and verification

The coordinator froze the shared action and protocol before assigning disjoint
runtime, signed API and independent transport review/test lanes. The coordinator
owns adapter integration, documentation/checklists and final regression.

New acceptance includes 17 signed API tests, four independent transport tests,
seven workflow boundary tests and four actual Temporal tests. Actual tests cover
first/later paused boundaries, accepted cancellation across worker replacement,
blocked downstream effects, CANCELED terminal state, canceled snapshot,
closed-run receipt retry, changed content conflicts, normal execution isolation,
and active debug timer rejection. Histories replay with the current worker.

Full default regression: **1,684 passed, one expected Linux TCP TIME_WAIT test
skipped on Windows, 53 integration tests deselected** (285.73 seconds). The seven
final workflow boundary tests were added after that run collected tests; the
final focused cancellation run verifies all **28 passed** (11.23 seconds),
including those seven. Full actual Temporal regression: **53 passed, 205
deselected** (335.63 seconds); ignored `artifacts/test-results/cancel-temporal.log`
and `cancel-temporal.json` record completion with exit code zero.

Ruff lint and formatting (386 Python files), mypy (93 source files), source/wheel
build and `git diff --check` passed. Independent final review found no blocking
issues. No hosted CI, cloud deployment or browser acceptance was run.

## Remaining scope

Cancellation during running activities or human/timer waits, task cleanup,
expiry, background cancel recovery, React controls and full DB-A6 remain open.
This increment does not undo effects completed before the paused boundary.
Production identity/provider and browser acceptance are not inferred from these
local SDK/server and signed fixture tests.
