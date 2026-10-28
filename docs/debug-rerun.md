# Rerun a saved debug session

Read the saved session and select a new operation ID. Send a full debug start
request to its rerun endpoint:

```http
POST /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/PARENT_SESSION_ID/reruns
Content-Type: application/json
Authorization: Bearer TOKEN

{
  "operation_id": "rerun-1",
  "source_revision": 2,
  "workflow_id": "onboarding",
  "configuration_revision": "cfg-1",
  "input": {},
  "breakpoints": [],
  "mode": "real"
}
```

Use a revision and workflow that exist in your project. The workflow ID must
match the parent's workflow; the operation ID must be new within that project.
Supply inputs explicitly. Omitted inputs mean an empty object, not a copy of the
old session's values. The selected snapshot must pass the existing execution
readiness and input checks.

The response is a new session and execution. The original run continues
independently on its original definition; it is not canceled. Follow the returned
session ID for status, inspection and controls. The parent must have an
acknowledged start, but it does not need a running worker for the rerun request.
The new child still needs its normal worker for execution.

Retry an interrupted request using the same endpoint and body. It resumes or
returns the same child. Reusing the operation with a different body, another
parent or the normal create route conflicts. Each request rechecks the original
owner's current Dev `debug.execute` permission, including successful retries.
No activation or deployment is needed or changed.

Keep the complete debugger database, including `debug_sessions`, `debug_commands`
and `debug_reruns`, and other local stores during recovery. Do not restore only
session rows: deleting parent associations can lose rerun provenance. See
[ADR 0043](decisions/0043-explicit-debug-rerun.md). Browser controls remain open.
