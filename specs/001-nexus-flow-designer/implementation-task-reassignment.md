# Explicit task reassignment increment

Branch: `codex/018-task-reassignment`. Decision: [ADR 0016](../../docs/decisions/0016-task-reassignment.md).

## Scope

Add an explicitly authorized administrative command to replace or refresh an existing task's assignment snapshot before voting begins. Preserve the original creation request, atomically persist revisioned before/after assignment history and audit, and retain durable command receipts. Apply the effective snapshot to inbox filtering and subsequent decisions. Cap assignment changes at 100 per task.

Expose `POST /tasks/{id}/reassignments` only through the existing authenticated API with an explicitly configured assignment resolver. The new scoped `task.reassign` permission belongs to an organization administrator role. A missing or failed resolver fails closed; the local launcher does not configure it automatically.

## Parallel ownership

The coordinator freezes shared contracts and coordinates identity/API integration, manifests and checklist updates. Disjoint sub-agents implement the task types/service and unit tests, independent persistence acceptance, and this decision/evidence documentation plus read-only invariant review. Shared environment and dependency changes remain coordinator-owned.

## Verification

Verified locally on Python 3.12 using the existing `.venv/Scripts/python.exe -m` interpreter:

- Focused service and HTTP reassignment suites: 19 passed (13 service, 6 HTTP), 4.62 seconds.
- Full `pytest -q`: 706 passed, 1 Linux-only regression skipped on Windows, 11 integration tests deselected, 48.54 seconds.
- Identity policy coverage adds 12 role cases across Dev, UAT and Prod.
- Ruff lint and formatting passed (203 files); strict mypy passed (64 source files).
- `uv build`: source distribution and wheel built successfully.

Coverage includes explicit group refresh, unchanged creation retries, current-snapshot visibility and decision access, invalid and unavailable resolver results, stale revisions, first-vote exclusion, durable exact retries, shared command-ID collisions, the 100-change bound, rollback and absence of reassignment completion writes. Signed HTTP coverage checks verified actor attribution, permission boundaries, strict JSON-to-domain conversion and missing or failed providers. Models retain defaults for older task/event rows; existing decision receipt behavior is regression-tested. Independent read-only service, API and policy review found no blocking issue. Directory resolution holds a SQLite write transaction; production contention and provider timeouts remain separate verification.

`pytest -m integration nexusflow/tests/runtime -q`: 11 passed, 67 deselected, 40.57 seconds using the actual local Temporal server. Existing HTTP-start and launcher regressions remain verified; these checks do not establish reassignment or human-task completion integration. `git diff --check` passed. Hosted CI has not run for this uncommitted increment. Local provider tests do not establish live Entra or browser acceptance.

## Remaining scope

This bounded assignment operation does not complete the human-task feature. Automatic refresh, delegation, post-vote reassignment, live Entra providers, React task UI, configurable forms, attachments, reminders and actual Temporal task creation/completion remain open. Production performance and availability require separate provider and deployment verification.

