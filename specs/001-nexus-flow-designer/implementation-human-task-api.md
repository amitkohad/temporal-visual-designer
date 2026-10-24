# Human-task inbox and API increment

Branch: `codex/017-human-task-api`, based on merged PR #14. Decision: [ADR 0015](../../docs/decisions/0015-human-task-api.md).

## Scope

Add authorized assigned-task pagination and optional status/run filters to the durable human-task service. Scope and assignment filtering precede pagination; every page rechecks policy. A missing assignment resolver supports read/decision hosts while failing closed on creation.

Compose scoped inbox, detail, audit and decision routes through the existing authenticated API. An explicit optional task database provider opens request-bound services and closes them after use. Verified Entra user object IDs match assignment snapshots; token subjects and caller-supplied IDs cannot stand in for the approver. Existing role grants are unchanged.

## Parallel ownership

The coordinator froze the query/page and HTTP/provider interfaces before delegating disjoint lanes: module service/types and unit tests; API composition and signed HTTP tests; independent SQLite inbox/restart acceptance and read-only API review. The coordinator owns identity integration, decision documentation, checklist reconciliation and aggregate validation. Agents use the existing interpreter without environment synchronization.

## Verification

Verified locally on Python 3.12:

- `pytest nexusflow/tests/api nexusflow/tests/human_tasks -q`: 71 passed (33 API, 38 human-task cases).
- `pytest -q`: 675 passed, 1 Linux-only regression skipped on Windows, 11 integration tests deselected (52.84 seconds).
- `pytest -m integration nexusflow/tests/runtime -q`: 11 passed using the actual local Temporal server, including HTTP execution and launcher restart regression (36.60 seconds). This preserves existing runtime behavior; human-task completion delivery is not yet integrated.
- Ruff lint/format passed (199 files); strict mypy passed (64 source files).
- `uv build`: source distribution and wheel built successfully.
- `git diff --check`: passed.

The pytest, Ruff and mypy commands used the existing `.venv/Scripts/python.exe -m` interpreter. Independent acceptance covers filtering before pagination, stable ordering, scope isolation, assignment snapshots, authorization and durable command retries after reopening SQLite. Signed HTTP cases prove object-ID eligibility even when the token subject differs, group-scoped grants, revocation, strict request boundaries, command conflicts and sanitized missing/failed providers. Identity tests also reject an otherwise authorized signed workload at the user-object-ID boundary. Independent read-only API review found no blocking issues. These are local provider tests, not live Entra or production readiness evidence; hosted CI has not run for this uncommitted increment.

## Remaining scope

This is a bounded subset of HT-FR-07 and task HTTP integration. Full HT-T01/02/04/05 and NF-T010 remain open. There is no React inbox, due-date/workflow filtering, requester progress view, forms/attachments, reminders, delegation, live directory integration or actual Temporal completion adapter. The local launcher does not enable the optional task database or create approval tasks. Hosted CI and production readiness are not inferred from local tests.
