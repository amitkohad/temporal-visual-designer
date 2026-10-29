# ADR 0048: Atomic metadata baseline for debug reconnect

Status: accepted for the sequential Dev debugger.

Add `GET /projects/{project_id}/debug-sessions/{session_id}/reconnect` without
query parameters. One synchronous, read-only Temporal query returns runtime
snapshot, active breakpoint set and current event retention window together.
The public response adds `session_id` and `next_after == latest_sequence`.
Current owner/Dev permission, acknowledged dispatch, saved placement and exact
run binding remain mandatory.

Clients render this baseline and resume event pagination after its cursor.
Transitions after that atomic query are later events, even if they happen before
the HTTP response arrives. This replaces the conservative multi-read stable
sequence procedure from ADR 0047. On a new retention gap, fetch a new baseline
rather than apply an old tail over current state.

The query copies immutable metadata in one callback. It does not combine
separate snapshot, breakpoint and event RPCs, append events or issue controls.
Service validation checks snapshot binding/state/revision, planned current node,
ordered unique completed-node prefix, canonical executable breakpoints and the
exact retained-tail window. Missing or inconsistent provider data fails closed.
No source/catalog/readiness providers are consulted.

A zero-event window is valid only for starting state at revision zero with no
completed nodes. Starting state with a nonzero cursor is also rejected; a completed
or paused snapshot cannot masquerade as a fresh replay baseline.

The reconnect response has a 32 KiB metadata budget: it can contain both the
proof plan's at-most-100 completed node IDs and breakpoint IDs. Existing 16 KiB
event-page and value-inspection budgets are unchanged. Inputs, outputs, errors,
actor IDs and event payloads are absent. Status remains payload-free.

This additive query introduces no event-ordering, history-command, database,
plan/result-format or dependency changes. Old workers lacking it fail safely;
existing status and event routes remain available with their prior semantics.
Runtime/history availability is required, including for terminal snapshots.

React consumers, streaming, detailed attempt/failure diagnostics, dynamic
variables, parallel reconciliation and production-provider readiness remain open.
