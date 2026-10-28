# Cancel a paused debug session

Use the existing signed Dev command endpoint after reading the session status:

```http
POST /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/commands
Content-Type: application/json
Authorization: Bearer TOKEN

{"command_id":"cancel-1","node_id":"first","action":"cancel","expected_revision":0}
```

Use the node and revision from the paused snapshot. The response is the existing
command receipt. Retry failures with the exact same body and command ID; changing
the body under that ID conflicts. Every request requires the original owner's
current `debug.execute` permission. UAT and Prod are rejected.

Cancellation keeps the step blocked, acknowledges a durable cancel intent, then
requests cancellation of the saved exact Temporal run. The status can briefly
show `cancelling` before `cancelled`. A successful receipt acknowledges the
request, not synchronous completion. If delivery fails, retry the same command;
there is no background cancellation recovery worker in this increment.

Only paused debug boundaries are supported. Running activities, timer/human
waits, managed-task cleanup and automatic expiry require later work. No effects
already performed by earlier steps are reversed. Session discovery continues to
report dispatch metadata (`started`), not terminal execution state; use the
status endpoint for runtime state. See [ADR 0041](decisions/0041-paused-debug-cancellation.md).
