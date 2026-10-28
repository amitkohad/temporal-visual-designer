# Cancel a debug session at a safe boundary

Use the existing signed Dev command endpoint after reading the session status:

```http
POST /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/commands
Content-Type: application/json
Authorization: Bearer TOKEN

{"command_id":"cancel-1","node_id":"first","action":"cancel","expected_revision":0}
```

Use the node and revision from a paused, `waiting_timer`, or undecided unmanaged
`waiting_approval` snapshot. The response is the existing
command receipt. Retry failures with the exact same body and command ID; changing
the body under that ID conflicts. Every request requires the original owner's
current `debug.execute` permission. UAT and Prod are rejected.

Cancellation keeps the step blocked, acknowledges a durable cancel intent, then
requests cancellation of the saved exact Temporal run. The status can briefly
show `cancelling` before `cancelled`. A successful receipt acknowledges the
request, not synchronous completion. If delivery fails, retry the same command;
there is no background cancellation recovery worker in this increment.

Paused boundaries, timer waits and unmanaged approval waits are supported. A
timer that expires after cancellation acceptance stays blocked before step
completion or downstream execution. Cancellation never supplies an approval.
If a competing approval already won, cancellation at that wait conflicts.

Runs containing a rejected wait-cancel from before this increment retain their
old wait-cancel behavior during replay and migration. New runs support the
extended waits; existing paused cancellation is unchanged.

Running activities, managed human-task waits/cleanup and automatic expiry require
later work. No effects
already performed by earlier steps are reversed. Session discovery continues to
report dispatch metadata (`started`), not terminal execution state; use the
status endpoint for runtime state. See [ADR 0041](decisions/0041-paused-debug-cancellation.md)
and [ADR 0042](decisions/0042-debug-wait-cancellation.md).
