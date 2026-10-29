# ADR 0047: Bounded metadata-only debug event replay

Status: accepted for the sequential Dev debugger.

Atomic reconnect is now added by [ADR 0048](0048-atomic-debug-reconnect.md).
The multi-read reconciliation procedure below describes the original event-only
subset and remains a legacy fallback.

Provide `GET /projects/{project_id}/debug-sessions/{session_id}/events` with an
exclusive integer `after` sequence (default zero) and `limit` (1–25, default 25).
Every read requires current owner/Dev permission, acknowledged session and saved
placement/run binding. It is a bounded query API, not a push stream or audit log.

Runtime events contain sequence, fixed kind, planned node ID (or terminal null),
fixed state and command revision. Kinds cover step start/pause/wait/completion,
accepted commands and execution completion/failure/cancellation. No workflow or
activity values, exception messages, actor IDs or wall-clock timestamps are
recorded. Successful command retries and rejected commands add no events.
Step completion uses transient running event state without changing workflow
state or history timing.

Sequence starts at one and increases on each event. Keep the latest 256 events
in workflow state, reconstructed deterministically from Temporal history on
replay. Instrumentation introduces no history commands, plan/result changes or
database migration. Normal executions do not expose debug events.

Event instrumentation and ordering are a replay compatibility contract. Future
changes must preserve existing sequence semantics or introduce explicit
versioning; inserting new events into old executions without that decision can
invalidate saved client cursors.

One read atomically returns a snapshot-bound page with earliest/latest sequence,
next exclusive `next_after`, `has_more` and `gap`. If `after` is older than
`earliest_sequence - 1`, set `gap` and return from the retained oldest event.
Clients must reconcile current status and active breakpoints before resuming
live state updates; an old retained tail must not overwrite a newer snapshot.
There is no public atomic snapshot/cursor handoff in this increment. A client can
read status/breakpoints between two event reads and accept the baseline only
when the latest sequence stays unchanged; otherwise retry with backoff or keep
showing authoritative status. Future cursors beyond latest are invalid. An empty history has
earliest/latest/next sequence zero and no gap. `after == latest` returns an empty
page with the same next cursor.

The adapter and service validate exact-run snapshot binding, full retained-tail
window, contiguous page sequences, fixed kinds/states, planned IDs, nondecreasing
event revisions bounded by snapshot revision, cursor consistency and page bounds.
Public pages include no raw snapshot and fit the 16 KiB response budget. Invalid
provider data fails closed without being echoed. Query parameters are bounded
ASCII decimal integers; duplicate and unknown parameters are rejected.

Runtime query/history availability is required. The retained tail is not a
permanent audit trail and does not outlive Temporal history retention. SSE,
WebSockets, attempt/cause diagnostics, event payloads, parallel frontiers,
production telemetry and React reconciliation remain open.
