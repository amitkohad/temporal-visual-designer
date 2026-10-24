# Temporal task completion delivery increment

Branch: `codex/023-task-completion-bridge`, from main after PR #20. Decision: [ADR 0021](../../docs/decisions/0021-task-completion-delivery.md).

## Scope and frozen boundaries

Deliver existing persisted human-task completions to an explicitly registered exact Temporal proof-runtime approval invocation. Add an asynchronous task outbox port, immutable destination registration, durable dispatch/acceptance journal and a dedicated scope/run/definition-bound workflow update. Preserve current synchronous delivery and manual proof controls.

The async sink accepts the existing `Completion`; its task ID, logical run, invocation and definition revision must match the trusted destination. The current adapter maps logical run to Temporal workflow ID and definition revision to pinned release ID. Retries preserve the original command/update identity. Local acknowledgment and audit commit together; cancellation or uncertain failure leaves the task outbox pending.

## Parallel ownership

The coordinator freezes interfaces, owns documentation/checklists and aggregate verification. One agent owns human-task async delivery and its unit tests; another owns runtime types, update, journal/adapter and unit tests; a third owns independent actual-Temporal integration tests. Paths are disjoint and dependency changes remain coordinator-owned.

## Verification

Verified locally using the existing Python 3.12 environment:

- Default `pytest -q`: 817 passed, one Linux-only test skipped on Windows (62.35 seconds). Integration tests are excluded by default.
- Focused async outbox and adapter suite: 15 passed (1.49 seconds).
- Full actual Temporal runtime suite: 19 passed, 77 deselected (39.53 seconds), including eight new task-completion scenarios and eleven existing runtime/API/launcher regressions.
- Ruff lint and formatting passed for 230 Python files; strict mypy passed for 69 source files.
- `uv build` produced the wheel and source distribution; `git diff --check` passed.

Independent acceptance covers real SQLite decisions through Temporal approval/rejection, lost acknowledgments both before and after local journal persistence, terminal update-result recovery after reopening stores, invalid scope/digest/run bindings and two approval invocations across worker restart and history replay. Async tests cover cancellation, audit rollback, cross-connection concurrent acknowledgment and scoped ordering. Adapter tests cover immutable routing/content, malformed receipts and missing retained history.

The actual Temporal suite exposed a readiness rejection that poisoned a stable update ID. A pure pre-acceptance workflow validator now rejects early invocation attempts; the same completion then succeeds when its invocation becomes current. The handler also rechecks validation before mutation.

No production-provider, browser, hosted CI or full workflow/task bridge acceptance is inferred from these results. Changes remain uncommitted pending the next requested commit/PR step.

## Remaining scope

Automatic workflow-to-task creation and assignment compilation, durable task binding before participant exposure, prevention of manual approval bypass for task-managed nodes, worker scheduling and local-launcher composition remain open. The complete interpreter, custom activity isolation, React applications and production adapters retain their existing gates.
