# Change breakpoints on a paused debug session

Read the current set:

```http
GET /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/breakpoints
Authorization: Bearer TOKEN
```

The response contains `session_id`, `revision` and sorted `breakpoints`. Use the
node and revision from the current paused status to replace the set:

```http
POST /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/breakpoints
Content-Type: application/json
Authorization: Bearer TOKEN

{"command_id":"breakpoints-1","node_id":"review","expected_revision":0,"breakpoints":["notify"]}
```

The response is a durable command receipt. Replacement increments the shared
runtime revision. Refresh status before sending step-over or continue. Removing
the current node does not resume it; use a separate control command when ready.
An empty list clears the set. Targets must be executable nodes in the saved plan,
with no duplicates and at most 100 entries. Start/end nodes are ineligible.

Retry an interrupted change with the same body and command ID. Reordering an
otherwise identical set is equivalent. Reusing an ID for a different set or a
step/continue/cancel command conflicts. Every request requires the original
owner's current Dev permission. Responses use `Cache-Control: no-store`.

The active set survives worker restart and is separate from the immutable source
and original start snapshot. Changes affect future boundaries only; they do not
rewind completed steps. Replacement during running work or active waits is not
supported. Queries require the runtime to be available. See
[ADR 0046](decisions/0046-paused-breakpoint-controls.md).

Browser breakpoint controls and parallel frontier behavior remain open.
