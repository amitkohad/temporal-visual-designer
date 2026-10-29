# Frozen activity input inspection increment

Branch: `codex/047-debug-step-input-inspection`, based on main `c99463c`
(merged PR #44).

## Implemented boundary

Add a typed no-store signed Dev read at
`GET /projects/{project_id}/debug-sessions/{session_id}/steps/{node_id}/input`.
The session must be acknowledged and owned by the currently authorized caller.
The node must be an activity in the digest-verified original source and saved
plan, with matching activity identity/version/artifact digest.

Inspect the saved literal `Step.input_json`, comparing its strict JSON object
with the original node's input mapping. It is the value dispatched through
`ActivityCall.input_json`, separate from `workflow_input_json`. No workflow input
merge, expression evaluation, connection/secret resolution or runtime query is
performed. The selected activity need not have run. Worker outage and completion
do not change the saved projection.

Use the exact catalog manifest's input schema after checking scoped reference,
descriptor reference and logical bundle digest. Later drafts or catalog versions
cannot widen an old session's visibility. Projection reuses public classification,
protected parent/leaf precedence, hidden unknown names, dual array opt-ins and
position-preserving bounds. Malformed, duplicate-key, nonfinite, non-object or
oversized raw inputs fail safely; plan/source mismatch also fails closed. The
16 KiB response cap includes node/activity metadata and has the existing empty
root-redacted truncation fallback.

See [ADR 0045](../../docs/decisions/0045-frozen-activity-input-inspection.md) and
[walkthrough](../../docs/debug-step-inputs.md). No runtime port, plan/result,
history command, database schema or dependency changes.

## Parallel ownership and verification

Service/types/unit, signed API and actual Temporal acceptance lanes have disjoint
ownership after contract freeze. The coordinator owns docs, checklists and final
review/regression. Existing validation rules remain intact: synthetic protected
values use permitted literal field names rather than bypassing prohibited
embedded-credential key checks.

Focused verification: 36 new core tests passed. Combined step-input, output,
workflow-input and array privacy regression: 155 passed (7.32 seconds). The 14
new signed API tests passed (11.30 seconds), including worker-independent reads,
fresh permission/owner/Dev checks, pending dispatch guards and response bounds.
The actual signed Temporal scenario passed (11.82 seconds), verifying inspection
before execution and during worker outage with runtime client access prohibited,
schema stability across edits, exact original literal dispatch separate from
workflow inputs, restart, completion, revocation and history replay.

Independent final privacy reviews found no blockers. Ruff lint and formatting
(413 Python files), mypy (95 source files), package source/wheel build and
`git diff --check` passed. Full actual Temporal regression: **61 passed, 236
deselected** (459.84 seconds). Ignored `artifacts/test-results/step-input-temporal.log`
and `step-input-temporal.json` record exit code zero. Full default regression:
**1,875 passed, one expected Linux TCP TIME_WAIT test skipped on Windows, 61
integration tests deselected** (556.57 seconds).
No hosted CI or browser acceptance was run.

## Remaining scope

General runtime variables, dynamically resolved inputs, connection/secret
resolution, in-flight/failed attempt inspection, events, mock connections,
parallel stepping and React controls remain open. Tests use local synthetic
identity and registered proof activities; custom Python worker isolation,
production providers and browser acceptance are not inferred.
