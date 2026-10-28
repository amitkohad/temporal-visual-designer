# ADR 0041: Durable cancellation of paused Dev debug sessions

Status: accepted for the sequential proof runtime at a paused boundary.

Extend the existing debug command action with `cancel`. Command ID, node ID,
expected revision, owner/Dev authorization, exact-run binding and persisted
receipt formats stay unchanged. The runtime accepts cancellation only while
paused at the named node with the current revision. Normal executions, running
activities and human/timer waits are outside this increment.

Acceptance records a durable update receipt and changes the runtime snapshot to
`cancelling`, without releasing the paused step. Other controls cannot release
this gate. The adapter then requests Temporal cancellation against the pinned
run, with a bounded RPC. Only after this acknowledgement does the service save
the local receipt. The normal cancellation handler records `cancelled`, and
Temporal records a canceled execution rather than a successful result.

If either acknowledgement is lost, the same command retrieves its original
Temporal update result and repeats the exact-run cancellation request. A saved
local receipt bypasses runtime calls after fresh authorization. Acceptance means
cancellation was requested; it does not promise synchronous terminal state.
A transient failure can leave a session `cancelling` until the caller retries.
No background recovery sweep is introduced.

This adds no persisted columns, plan fields or runtime version changes. Existing
step/continue histories must replay unchanged. Package activation is unaffected.
Cancellation of already dispatched side effects, managed task cleanup, waiting
states, automatic expiry, React controls and full DB-A6 remain open.
