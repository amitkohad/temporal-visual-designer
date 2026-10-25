# Local managed-approval application increment

Branch: `codex/026-local-managed-approvals`, from main after PR #23. Decision: [ADR 0024](../../docs/decisions/0024-local-managed-approvals.md).

## Bounded scope

An explicit installation-locked `enable-tasks` command configures persistent local task/journal stores, a Dev-only synthetic participant and an installation-bound feature marker. Existing initialization, default welcome workflow and disabled launcher behavior remain compatible. Enabled application composition supplies the task API, trusted assignment resolver, task creation activity and owned background completion dispatcher.

Read-only profile/schema/placement validation precedes runtime startup and writable constructors. Repeated enable preserves identity revocation and data; failed partial initialization and missing stores fail closed. API decisions use fresh signed authorization; the local worker cannot grant participant permissions or impersonate other organizations.

## Parallel ownership

The coordinator owns CLI integration, sample project, documentation, task checkboxes and aggregate verification. One agent owns feature initialization, bootstrap provider wiring and isolated acceptance. A second owns runtime lifecycle composition and failure-path tests. A third owns independent real CLI/socket/Temporal acceptance. Writable paths are disjoint and dependency changes remain coordinator-owned.

## Verification

Verified locally using the existing Python 3.12 environment:

- Focused feature/bootstrap suite: 40 passed, including read-only rejection of missing receipt/audit tables before runtime reservation or journal creation.
- Independent profile/runtime/CLI subset: 30 passed; the earlier complete local run passed 96 tests with one Linux-only test skipped on Windows, before the final two missing-table runtime cases were added.
- Final full default suite: 937 passed, one Linux-only test skipped on Windows and 33 integration tests deselected (88.99 seconds).
- New actual CLI/socket/Temporal module: four passed (62.46 seconds).
- Full actual Temporal runtime suite: 33 passed, 109 deselected (104.18 seconds), including all 29 previous runtime/API/launcher/managed-task/background-dispatch regressions.
- Ruff lint and formatting passed (251 Python files); strict mypy passed (73 source files). Wheel and source distribution builds and `git diff --check` passed.

Independent reciprocal reviews found no blockers in profile provisioning, grant revocation preservation, API/worker authorization separation, placement verification or lifecycle cleanup. Real acceptance publishes the supplied JSON example through the signed API, starts it, stops while approval is pending, restarts with the original token/task/execution and approves through HTTP. Background delivery completes the actual Temporal run and records one delivery audit. Exact decision/start retries preserve receipts. Repeated enable does not restore a revoked participant grant, and missing enabled files fail before recreation or startup.

No hosted CI, browser, live Entra or full production-provider acceptance is claimed. Changes remain uncommitted for the next requested commit/PR step.

## Remaining scope

React designer/inbox, configurable task form mapping, attachment and notification providers, cancellation reconciliation, production identities/group resolution, deployment packaging and production worker supervision retain their existing gates. The launcher remains a single-user synthetic Dev demo and does not establish full human-task or end-to-end business-process acceptance.
