# Background completion dispatch increment

Branch: `codex/025-completion-dispatch-worker`, from main after PR #22. Decision: [ADR 0023](../../docs/decisions/0023-completion-dispatch-worker.md).

## Bounded scope

Add trusted scoped completion batches with exclusive task-ID pagination, bounded sink waits and independent per-row failures. Preserve atomic acknowledgment/audit, destination idempotency, cancellation and existing direct-delivery behavior. Add an explicitly hosted dispatcher that rotates through pending pages, retries on later sweeps, reports payload-free health and owns orderly background cancellation.

Contracts: `CompletionBatch` has bounded attempted/delivered/failed counts and an optional next task-ID cursor. `TaskService.dispatch_completion_batch` accepts scope, async sink, limit, cursor and delivery timeout. `CompletionDispatcher` supplies `run_once`, `run`, `last_batch` and `last_error_code`; `run_completion_dispatcher` owns the background lifecycle. These internal worker ports do not grant participant or public HTTP access.

## Parallel ownership

The coordinator freezes shared result types and owns documentation, specification checkboxes and aggregate verification. One agent owns service paging, per-item failure isolation and unit tests; another owns the background loop/lifecycle and its independent unit tests; a third owns actual Temporal acceptance. Writable paths are disjoint and dependencies remain coordinator-owned.

## Verification

Verified locally on Python 3.12 using the existing environment:

- Full default suite: 904 passed, one Linux-only test skipped on Windows, 29 integration tests deselected (68.07 seconds).
- New bounded batch/lifecycle suite: 55 passed (3.26 seconds), including 23 batch cases and 32 worker cases. Existing and new human-task tests together: 188 passed.
- New actual Temporal background-dispatch module: four passed (3.52 seconds).
- Full actual Temporal runtime suite: 29 passed, 109 deselected (40.48 seconds), retaining all 25 previous runtime/API/launcher/managed-task regressions.
- Ruff lint and formatting passed (244 Python files); strict mypy passed (72 source files). Wheel/source builds and `git diff --check` passed.

Reciprocal independent reviews found no blockers in batch isolation, exact scoped payload validation, cursor traversal, cancellation, atomic acknowledgment/audit, concurrency, reservation-before-first-loop ownership or payload-free health. One minor duration-validation ordering issue was corrected so out-of-range large integers are rejected before floating-point conversion.

Actual Temporal tests demonstrate approve/reject progression without explicit manual delivery, broken-destination isolation with single-row pages, other-organization isolation and cancellation after durable destination acknowledgment. Closing and reopening both SQLite stores then restarting dispatch acknowledges the original completion against the closed workflow. Default tests additionally cover malformed rows, timeouts, missing acknowledgments, audit rollback, bounded SQL paging, duplicate ownership and retry pacing.

No hosted CI, live Entra or browser acceptance is inferred. Changes remain uncommitted for the next requested commit/PR step.

## Remaining scope

Local launcher/API composition, persisted retry schedules, distributed worker supervision, notifications, cancellation reconciliation, production identity/connectors and React screens remain open. This increment provides fixed-interval cooperative async retry with an in-memory cursor; it does not establish production throughput or fairness under unbounded concurrent arrivals.
