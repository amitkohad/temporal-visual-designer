# ADR 0045: Frozen activity input inspection

Status: accepted for literal activity mappings in the sequential Dev debugger.

Add `GET /projects/{project_id}/debug-sessions/{session_id}/steps/{node_id}/input`
with session/workflow/source/node/activity identity, sanitized `values`,
`redacted_paths` and `truncated`. Reads require current owner/Dev authorization
and an acknowledged session bound to the saved placement. Only activity nodes
in the exact original source and frozen execution plan are eligible.

The proof interpreter passes an activity's literal `Step.input_json` to
`ActivityCall.input_json`. Inspect this saved value, checking that its strict JSON
object matches the original node's literal input mapping. This is separate from
the workflow input object supplied through `ActivityCall.workflow_input_json`.
Do not merge them, evaluate expressions, resolve secrets or claim to show values
produced by a future general mapping engine.

Read the exact scoped catalog manifest, verify the descriptor activity reference
and logical bundle digest, then use its `input_schema`. The workflow input schema
and activity output schema are not substitutes. Later drafts/catalog versions
cannot widen visibility for an existing session. Missing or inconsistent
source/plan/catalog bindings fail closed.

Projection reuses the existing explicit-public rules, protected parent/leaf
precedence, hidden unknown names, dual array opt-ins and traversal limits. Raw
literal inputs must be strict JSON objects within 64 KiB. Duplicate keys,
nonfinite numbers and malformed values fail safely. The complete response,
including node/activity metadata, has a 16 KiB cap with empty/root-redacted
truncated fallback.

No runtime query or control is needed. Inspection works before dispatch of the
selected activity, during worker outage and after completion; it reports the
planned literal mapping, not attempts or live execution state. Existing status,
output inspection, history commands, storage and plan/result formats remain
unchanged.

General runtime variables, dynamic/resolved step inputs, connection/secret
resolution, failed attempts/events, parallel frontiers and React controls remain
open. Local signed and Temporal tests do not establish production-provider or
browser acceptance.
