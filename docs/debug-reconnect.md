# Reconnect with a consistent debug baseline

Fetch one metadata baseline:

```http
GET /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/reconnect
Authorization: Bearer TOKEN
```

The response contains `session_id`, `snapshot`, `breakpoints`,
`earliest_sequence`, `latest_sequence` and `next_after`. Snapshot includes current
node/state/revision and completed node IDs. It contains no values or error
messages. Responses use `Cache-Control: no-store`.

Render the returned state and active breakpoint set, then request
`/events?after=NEXT_AFTER&limit=25`. The baseline and cursor come from one runtime
query, so transitions that happen before the HTTP response arrives are still
available after that cursor. Drain pages while `has_more` is true.

If a page reports `gap`, discard it as a current-state baseline and fetch
`/reconnect` again. The retained tail can be displayed as historical information,
but must not overwrite newer state. Use the separate input/output inspectors for
values. Event sequence and command revision have different meanings; controls
still require the current node and revision and may conflict if another command
wins the race.

The endpoint works for active and terminal debug runs when runtime history is
available. Each read checks the original owner's current Dev permission and
targets the saved exact run. It makes no source/catalog/readiness requests or
workflow-control calls. The combined metadata response is capped at 32 KiB;
event pages and value inspectors retain their existing limits.

See [ADR 0048](decisions/0048-atomic-debug-reconnect.md). Browser consumers,
streaming and parallel-frontier reconciliation remain open.
