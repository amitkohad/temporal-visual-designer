# ADR 0042: Cancellation at safe debug wait boundaries

Status: accepted for Dev timer waits and unmanaged approval waits.

Extend ADR 0041's existing `cancel` action without changing API, plan, database
or receipt shapes. A debug run may accept cancellation at its current paused
boundary, timer wait, or undecided unmanaged approval wait. Commands still need
the matching node and revision and fresh owner/Dev authorization. Managed task
creation/waits and running activities remain ineligible.

Accepted cancellation sets a durable workflow-local cancellation flag and the
`cancelling` snapshot state. If the current wait finishes before the exact-run
Temporal cancellation RPC arrives, the interpreter blocks before completing the
step or starting the next one. This preserves the original node and prevents a
timer expiry from releasing downstream effects during delivery failure.

An unmanaged approval already decided is ineligible. Once cancel is accepted,
approval commands cannot manufacture a decision or advance the workflow. The
existing serialized updates and expected revision order competing commands.
The adapter's durable-acceptance-then-cancel protocol and retry behavior remain
unchanged. Proper cancellation closes the exact Temporal run as CANCELED.

The post-wait gate is conditional on an accepted cancellation command. Newly
eligible wait cancellation is protected by the Temporal patch marker
`debug-wait-cancellation-v1`; replay without that marker preserves the previous
rejection. This also protects histories containing failed cancel updates.
Paused cancellation retains its existing path. Replay of existing runtime
scenarios is required before completion.

The SDK caches the patch decision for an execution. If replay encounters a
pre-patch rejected wait-cancel update, that run conservatively continues rejecting
wait cancellation after migration. New executions use the new behavior. This
does not change paused cancellation or operator cancellation through Temporal.

DB-A6 is covered for this sequential proof subset only. Managed task cleanup,
running activity cancellation, idle expiry, background recovery, parallel policy
and React presentation remain open. Previously completed side effects are not
reversed.
