# Sequential approval increment

Branch: `codex/021-sequential-approvals`, based on merged PR #18. Decision: [ADR 0019](../../docs/decisions/0019-sequential-approvals.md).

## Bounded scope

Add fixed sequential approval stages to the existing human-task service and authenticated API. Accept 1 to 20 stages with unique IDs and any-one/all-approvers rules. Resolve all scoped assignments at creation, reject empty stages and cap the total resolved assignment entries at 1,000. Preserve immutable creation input and persist stage assignment snapshots, current index and stage-tagged decisions.

Current-stage eligibility governs reads, inboxes, forms and new decisions. Advance atomically after stage approval, permit repeated actors across different stages and reject the whole task immediately on any rejection. Only terminal decisions create completion outbox entries. Preserve shared revision conflict behavior, immutable exact command receipts and fresh authorization, including a narrow receipt retry for the original actor after their stage ends.

Keep reassignment restricted to before the first task vote, updating only the first stage's assignment. Retain one pinned shared form that becomes read-only after the first vote. Bind notification snapshots to stage identity so stage advancement cancels old pending notifications even with identical recipients.

## Parallel ownership

The coordinator freezes stage contracts and delegates disjoint work. One agent owns task types and persistence transitions, a second owns independent acceptance tests, and a third owns these decision/increment documents plus a read-only review. The coordinator owns authenticated API and notification integration, specification checkboxes and aggregate verification. Agents do not independently synchronize shared dependencies or commit changes.

## Verification

Verified locally on Python 3.12 using the existing `.venv/Scripts/python.exe -m` interpreter:

- Full `pytest -q`: 778 passed, 1 Linux-only regression skipped on Windows and 11 integration tests deselected (56.36 seconds).
- The new focused acceptance suite contains 25 cases: 21 independent service/contract cases, 2 signed HTTP cases and 2 stage-notification/legacy payload cases.
- Ruff lint and formatting passed (219 files); strict mypy passed (68 source files).
- `uv build`: source distribution and wheel built successfully.
- `pytest -m integration nexusflow/tests/runtime -q`: 11 passed, 67 deselected (31.33 seconds), using the actual local Temporal CLI/server to check existing API, launcher and runtime behavior. This is regression evidence, not human-task workflow integration proof.
- `git diff --check`: passed.

Independent read-only review found no blocking issue within this increment. Acceptance covers contract limits, scoped assignment failure, any-one/all-approvers progression, immediate rejection, final-only outbox creation, persistence across restart, repeated actors, current-stage inbox and API access, stale revisions and command retries, concurrent decisions, pre-vote reassignment, shared-form behavior and stage-bound notification cancellation. Existing non-sequential approvals retain their tested behavior.

Hosted CI has not run for this uncommitted increment. Local synthetic identity and notification-sink acceptance does not establish live Entra directory behavior, production transport or actual Temporal human-task progression.

Compatibility review includes preserving existing non-sequential serialized notification payloads when stage identity is absent. An existing inbox pagination test now derives its expected order from the documented `(created_at, task_id)` ordering instead of assuming distinct clock timestamps imply creation order; this changes the test's clock assumption without changing production ordering.

## Remaining scope

Conditional routes, dynamic stages, per-stage forms/deadlines, post-vote reassignment, delegation, React rendering/configuration, live directory providers and Temporal task creation/completion remain open. Notification transports and automatic scheduling retain their existing pending status.
