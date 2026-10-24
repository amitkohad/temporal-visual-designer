# Workflow-managed task creation increment

Branch: `codex/024-managed-task-creation`, from main after PR #21. Decision: [ADR 0022](../../docs/decisions/0022-managed-task-creation.md).

## Bounded scope

Opt-in approval steps compile strict assignment configuration, invoke a retryable trusted task-creation activity, and wait on the exact returned task ID. Register the exact Temporal completion destination and server/namespace before the task creation transaction becomes visible. Preserve assignment snapshots, creation retries, existing decision/form/stage behavior and the durable completion sink. Block manual approval bypass for managed steps.

Keep existing manual approvals and old history replay compatible. Preserve permanent dispatch fingerprints for pre-upgrade plans by omitting the empty additive configuration field when hashing. Do not infer complete provider or configuration attestation from activity metadata: the worker and persisted execution plan are trusted.

## Parallel ownership

The coordinator freezes transport types and owns start-fingerprint compatibility, documentation, checkboxes and aggregate verification. One agent owns configuration compilation and workflow behavior; a second owns provisioning, route registration and service transaction tests; a third owns independent real Temporal acceptance. Source and test ownership is disjoint. Dependency changes remain coordinator-owned.

## Verification

Verified locally on Python 3.12 with the existing environment:

- Full default suite: 849 passed, one Linux-only test skipped on Windows (62.65 seconds).
- New host/service tests: 17 managed-preflight cases, 14 creation-adapter cases and one pre-upgrade permanent-start-receipt case. Existing human-task regression suite: 133 passed.
- Independent managed Temporal module: six passed (4.34 seconds), covering automatic approve/reject, any/all/sequential rules, manual bypass rejection, wrong task ID rejection, activity lost-result retry, restart/replay and an actual old wire payload with the additive step field absent.
- Full actual Temporal runtime suite: 25 passed, 109 deselected (36.57 seconds), including all six new managed-task scenarios and nineteen existing runtime/API/launcher/completion regressions.
- Ruff lint and formatting passed (238 files); strict mypy passed (71 source files). Wheel and source distribution built successfully; `git diff --check` passed.

Independent reciprocal reviews found no blockers in compilation, workflow validation, task transaction visibility, route pinning, retry gaps or old receipt compatibility. Registration-before-visibility tests cover failed and uncertain registration acknowledgments, stable retries, metadata spoofing, fresh permission revocation, conflicting routes and concurrent provisioning. Legacy wire payload and dispatch hash regressions verify compatibility at separate boundaries.

No hosted CI, live Entra, browser or complete production feature acceptance is claimed. Changes remain uncommitted for the next requested commit/PR step.

## Remaining scope

Explicit host activity registration and completion dispatch are still required. Background delivery workers, local-launcher integration, task cancellation on workflow termination, task-bound form mapping, attachments, production identity/connectors and React inbox/designer configuration remain open. Full runtime control flow and full feature acceptance retain their existing unchecked tasks.
