# Find saved Dev debug sessions

After [enabling local debugging](local-development.md#enable-dev-debugging), reconnecting clients can discover their saved sessions without keeping each session ID:

```http
GET /projects/welcome/debug-sessions?limit=50
```

The signed request requires fresh Dev debug.execute permission. Only sessions created by the verified caller in that organization/environment/project are visible; there is no owner override. Empty authorized project scopes return an empty page.

The response contains `items` (the existing public DebugSession metadata) and `next_cursor`. To continue:

```http
GET /projects/welcome/debug-sessions?limit=50&cursor={next_cursor}
```

Treat the cursor as opaque and URL-encode it when constructing a URL. It is bound to your organization, environment, project and owner; malformed/foreign-context tokens return a safe error. It does not replace authorization. The default limit is 50, range 1–100. Each page is ordered by recorded creation time, newest first, then session ID as a deterministic tie-breaker. Dispatch acknowledgment does not move a session. Listing is not a frozen event snapshot; refresh the first page for newly created sessions.

Items contain identifiers, source/workflow/configuration identity, definition digest, owner/time and dispatch metadata. They contain no saved input, plan, connection data or runtime outputs. The `state` is dispatch_pending or started; started means dispatch was acknowledged, not that the workflow is currently running. Follow the chosen session ID through [status/control](designer-debug-sessions.md) or [redacted input inspection](debug-input-inspection.md).

Discovery does not query drafts, configuration or Temporal and can work when a worker is stopped. Missing configured providers or corrupt metadata return sanitized unavailable responses. Existing detail routes continue to verify complete snapshots and exact runtime bindings. UAT/Prod and revoked access are rejected.

The local profile/session database, journal and history remain the recovery unit. This route changes no records, permissions, activation, workflow state or retention. The React session list, runtime status projection, search, cancellation/expiry and event replay remain planned.
