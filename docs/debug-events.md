# Reconnect to debug progress

Read ordered metadata from an acknowledged Dev session:

```http
GET /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/events?after=0&limit=25
Authorization: Bearer TOKEN
```

The response includes `session_id`, `events`, `earliest_sequence`,
`latest_sequence`, `next_after`, `has_more` and `gap`. Each event contains only
`sequence`, `kind`, `node_id`, `state` and `revision`. Values and error messages
are absent. Responses use `Cache-Control: no-store`.

Apply events in sequence and use `next_after` in the next request. Read another
page when `has_more` is true. Commands advance the shared runtime revision;
event sequence also advances for workflow transitions, so the two counters have
different meanings. Reads never advance execution or release a paused step.
Successful command retries and rejected controls add no duplicate events.

Only the latest 256 events are retained. When `gap` is true, treat the returned
tail as historical information and refresh current status and breakpoints. Do
not apply that old tail over a newer status snapshot. This increment has no
atomic public snapshot/cursor handoff: a client can read status/breakpoints
between two event reads and resume from the latest sequence only if it stayed
unchanged. If it moved, retry with backoff or continue displaying authoritative
status. Browser reconciliation remains future work.

Use the separately authorized input/output inspectors for values. A cursor beyond
the latest sequence is invalid; reset to zero for a different session. Never mix
cursors between sessions.

Every page checks the original owner's current Dev permission and targets the
saved exact run. Worker restart reconstructs metadata through history replay;
queries require an available compatible runtime and retained Temporal history.
This endpoint provides pagination rather than a push stream or permanent audit
trail. See [ADR 0047](decisions/0047-debug-event-replay.md).

Browser reconciliation, live streaming, attempts and detailed failure diagnostics
remain open.
