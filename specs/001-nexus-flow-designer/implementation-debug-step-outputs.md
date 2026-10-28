# Completed activity output inspection increment

Branch: `codex/046-debug-step-output-inspection`, based on main `7fc3f9a`
(merged PR #43).

## Implemented boundary

Add a typed no-store signed Dev read for one completed activity output at
`GET /projects/{project_id}/debug-sessions/{session_id}/steps/{node_id}/output`.
Every read checks current permission, original ownership and saved placement.
Only an activity in the exact original source and frozen plan is eligible.

A read-only Temporal query returns one selected result and binding metadata on
the saved exact run. The workflow retains its existing bounded result dictionary
on the instance, reconstructing it through replay without new history commands
or plan/result changes. Reads do not release gates, dispatch activities or change
activation. The trusted raw-output transport is internal to the inspection path.

The service resolves the pinned scoped activity manifest, verifies the descriptor
reference and recomputes its logical bundle digest before trusting its output
schema. This prevents a changed stored schema from declassifying fields under an
old artifact reference. New drafts/versions do not alter an old session's schema.
Missing or inconsistent metadata fails closed.

Strict JSON object results use the existing explicit-public redaction engine,
including protected flag precedence, hidden unknown names, array dual opt-ins,
position preservation and traversal limits. Complete responses including
activity/node metadata fit 16 KiB; overflow falls back to empty values with root
redaction and truncation. Duplicate keys, nonfinite constants, malformed JSON and
raw results above 64 KiB are safe failures. Raw values are not persisted in the
debugger database or returned by status/discovery.

See [ADR 0044](../../docs/decisions/0044-debug-step-output-inspection.md) and
[walkthrough](../../docs/debug-step-outputs.md).

## Parallel ownership and verification

Shared response/runtime contracts were frozen before disjoint runtime/transport,
service/projection and signed API lanes. The runtime lane adds actual signed
HTTP/Temporal privacy and restart acceptance after the providers are ready.
The coordinator owns shared contracts, documentation/checklists and final review.
Independent final privacy review found no blocking issues.

Focused verification: 34 new service tests, 12 new transport tests and 21 new
signed API tests passed. The combined service/input/array/session suite passed
133 tests (4.26 seconds), preserving prior redaction behavior. Two actual runtime
tests passed (4.26 seconds), covering selected output reads, incomplete/nonactivity
guards, normal-run denial, worker replacement, closed-run reads and replay.
The additional actual signed API test passed (5.76 seconds), verifying sanitized
public status/array leaves, hidden protected/unknown values, unchanged paused
state, original schema despite later draft/catalog versions, restart, completion,
fresh revocation and history replay.

Full default regression: **1,825 passed, one expected Linux TCP TIME_WAIT test
skipped on Windows, 60 integration tests deselected** (331.52 seconds). Full
actual Temporal regression: **60 passed, 236 deselected** (346.52 seconds).
Ignored `artifacts/test-results/step-output-temporal.log` and
`step-output-temporal.json` record exit code zero.

Ruff lint and formatting (406 Python files), mypy (94 source files), package
source/wheel build and `git diff --check` passed. No hosted CI or browser
acceptance was run.

## Remaining scope

General runtime variables, step-input inspection, scalar output roots,
in-flight/failed attempt results, event replay, mock connections, expiry,
parallel frontiers and React controls remain open. Custom Python execution
isolation and production providers are not established by registered synthetic
activity and signed local identity fixtures. Runtime output reads require the
Temporal query to succeed; they are not the worker-independent frozen-input API.
