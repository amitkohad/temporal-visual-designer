# ADR 0044: Schema-bound inspection of completed activity outputs

Status: accepted for the sequential Dev debugger.

Add `GET /projects/{project_id}/debug-sessions/{session_id}/steps/{node_id}/output`.
Fresh owner/Dev authorization and saved placement/run binding are mandatory.
Only completed activity nodes in the frozen source/plan are eligible. The read
does not grant a permit, run an activity or modify activation.

The workflow retains its existing bounded activity-result dictionary on the
instance and exposes one selected output through a read-only query. No execution
plan, history command or result format changes. Normal non-debug runs cannot use
this query. The trusted adapter validates exact-run response metadata, selected
node, completion and the existing 64 KiB raw-output bound before projection.
Raw results never enter the public response or debugger database.

The service verifies the original source definition digest, activity node and
pinned activity reference. It loads that exact scoped catalog manifest, checks
the descriptor reference and recomputes its logical bundle digest before using
the output schema. Later drafts or newer activity versions cannot change the
old session's schema. Protected/unsupported/unclassified fields remain hidden;
the workflow-level output schema is not a substitute for the activity schema.
Missing or inconsistent source/catalog/runtime metadata fails closed.

Only strict JSON objects are supported. Duplicate keys, nonfinite constants,
malformed JSON and oversized raw results fail safely without returning payloads.
Projection reuses the input inspector's explicit public leaf classification,
protected flag precedence, dual array opt-ins and bounded traversal. The full
output response, including node/activity metadata, must fit 16 KiB; overflow
returns an empty projection, root redaction pointer and truncation flag.

Inspection requires the runtime query to succeed. Worker outage behavior is not
the worker-independent frozen-input path. Metadata-only status/discovery remain
unchanged. Catalog lifecycle state alone does not hide a retained pinned schema;
the exact immutable reference and digest remain required.

General variables, step inputs, in-flight/failed attempt outputs, scalar output
roots, event replay, parallel frontiers, React controls and production secret
providers remain open. Local Temporal and synthetic signed identity tests do not
establish browser or production-provider acceptance.
