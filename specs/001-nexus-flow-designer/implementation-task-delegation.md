# Participant task delegation increment

Branch: `codex/022-task-delegation`, based on merged PR #19. Decision: [ADR 0020](../../docs/decisions/0020-task-delegation.md).

## Bounded scope

Allow an eligible participant to transfer their own uncast current-stage vote to one resolved user while a task is pending. Require fresh scoped `task.respond` authorization, an expected revision and a unique command ID. Reject self-delegation, existing eligible targets, group targets, cast-vote changes and provider failures. Preserve all other and earlier votes, immutable creation input and future stage snapshots.

Persist the assignment change, before/after audit and receipt atomically without creating a completion entry. Share the 100-change assignment history limit and command-ID namespace with existing operations. Remove the delegator's current participant access while preserving their exact historical receipt retry after fresh scope authorization. A delegated recipient receives no implicit authorization grant.

Expose the authenticated API through the existing optional directory resolver. Preserve shared-form read-only behavior after any vote and stale notification cancellation. Keep administrative reassignment restricted to its existing pre-vote boundary.

## Parallel ownership

The coordinator freezes contracts and owns API integration, specification checkboxes and aggregate verification. One agent owns domain types and service transitions, another owns independent acceptance tests, and a third owns this increment/decision documentation and read-only review. Writable paths are disjoint. Dependency synchronization and commits remain coordinator-owned.

## Verification

Verified locally on Python 3.12 using the existing `.venv/Scripts/python.exe -m` interpreter:

- Full `pytest -q`: 802 passed, 1 Linux-only regression skipped on Windows and 11 integration tests deselected (118.29 seconds).
- New focused acceptance: 24 passed (4.99 seconds), covering 18 independent service cases, 5 signed HTTP cases and 1 forms/reminders service integration case.
- Ruff lint and formatting passed (224 files); strict mypy passed (68 source files).
- `uv build`: source distribution and wheel built successfully.
- `pytest -m integration nexusflow/tests/runtime -q`: 11 passed, 67 deselected (62.96 seconds), using the actual local Temporal CLI/server. These checks verify existing API, launcher and runtime behavior; they do not establish a human-task workflow bridge.
- `git diff --check`: passed.

Independent read-only review found no blocking issue in the service, contracts or API boundary. Acceptance covers actor binding, independent recipient authorization, participant access removal, durable historical retries after fresh authorization, before/after audit, transaction rollback, cross-command collisions, bounded assignment changes, concurrent commands, cast-vote preservation, sequential snapshots, frozen shared forms and stale notification cancellation. Existing terminal decisions remain the only source of completion entries.

Hosted CI has not run for this uncommitted increment. Local synthetic identity, directory and notification-sink acceptance does not establish live Entra behavior, external transport delivery or Temporal human-task workflow progression.

## Remaining scope

Automatic delegation, claim/release, conditional routes, cast-vote rewriting, React rendering and configuration, live directory providers and Temporal human-task creation/completion remain open. Notification transports and automatic scheduling retain their existing pending status.


