# Human-task form integration increment

Branch: `codex/019-task-forms`, based on merged PR #16. Decision: [ADR 0017](../../docs/decisions/0017-task-forms.md).

## Bounded scope

Bind a pinned configurable form to a task through a trusted internal operation, provide permission-filtered previews and revision-checked submissions, and prevent approval before a bound form has a successful server-validated submission. Reuse the real Designer form engine for typed schemas, conditional visibility and field permissions. Keep values separate from ordinary task state projections and audits.

Forms share task revisions and SQLite transactions. Submission and decision races cannot approve an unvalidated form or overwrite a decision. Editing ends at the first vote. Reassignment continues to update effective eligibility, so removed approvers lose form access. Binding preserves original task creation identity and is idempotent against original binding inputs.

## Parallel ownership

The coordinator froze table columns, field-policy and service/API interfaces before delegation. Separate sub-agents own the form gateway/service, independent SQLite acceptance tests, and HTTP integration/signed request tests. The coordinator owns task-core approval guards, schema setup, event kinds, documentation and checklist reconciliation. Existing environments remain unchanged during parallel checks.

## Verification

Verified locally on Python 3.12 using the existing `.venv/Scripts/python.exe -m` interpreter:

- Full `pytest -q`: 735 passed, 1 Linux-only regression skipped on Windows, 11 integration tests deselected (46.79 seconds).
- Independent task-form acceptance: 17 passed. Signed HTTP form tests: 12 passed; the full API suite has 51 passing cases.
- Ruff lint and formatting passed (209 files); strict mypy passed (66 source files).
- `uv build`: source distribution and wheel built successfully.
- `pytest -m integration nexusflow/tests/runtime -q`: 11 passed with the actual local Temporal server (36.45 seconds), preserving existing HTTP execution and launcher behavior without claiming human-task form mapping.
- `git diff --check`: passed.

Tests cover privacy, schema validation, authorization and field-policy revocation, immutable binding, shared revisions, SQLite restart, concurrent decisions and atomic rollback. Additional regressions verify nonempty conditional-rule arrays and merged-value size enforcement. Parent review corrected JSON binding validation and preservation of identity denial errors before aggregate verification. Independent core review found no blocking issue in the approval guard, policy checks or transaction boundaries. Hosted CI has not run for this uncommitted increment; these tests do not establish actual task/runtime or browser integration.

## Remaining scope

This increment does not complete the full human-task feature. Attachments/scanning, reminders/escalations, delegation, sequential/conditional approval routing, React form rendering/accessibility, production field-policy and directory adapters, actual released-form resolution, workflow variable mapping and Temporal task creation/completion remain open. Existing runtime regression tests preserve workflow behavior without establishing task-form integration with Temporal.
