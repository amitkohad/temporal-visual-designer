# ADR 0039: Owner-scoped debugger session discovery

Status: accepted for the bounded local backend increment.

Saved sessions survive restart, but reconnecting clients currently need to retain each returned session ID. Add read-only, metadata-only discovery for an authorized owner within a project. This does not add workflow status projections or event replay.

## Scope and storage

Every list request requires fresh debug.execute and explicit Dev authorization. Use the verified actor as the owner predicate; never accept a caller-selected owner. Scope the SQL by organization, environment and project before returning typed DebugSession metadata. The query selects only session ID and public session JSON, not request input, frozen plan, placement or checksum columns. It does not query project drafts, configuration or Temporal. Existing detail/control/inspection routes keep their full ownership, binding and integrity checks.

No project-existence check is needed for discovery: an empty authorized project scope returns an empty page. No table/schema migration, snapshot rewrite, lifecycle change or new grants are introduced. The existing per-project session quota bounds the local dataset.

## Pagination

Sort by immutable recorded creation timestamp descending, then session ID descending to break ties. Use an exclusive composite cursor and SQL limit+one, never offset or an unstable implicit row ID. Limits are 1–100, default 50. The cursor is bounded strict URL-safe encoded JSON, bound to organization, environment, project and verified owner; malformed or cross-context cursors fail safely. Encoding is not a signature or authorization credential: every request still applies fresh permission and owner filters.

Acknowledgment of a pending dispatch cannot change its pagination position. Pages are not a frozen event snapshot; clients refresh the first page to discover new sessions. Cursor order follows recorded creation time, not update time or a distributed commit sequence.

## Meaning and limitations

Metadata state remains dispatch_pending or started, describing dispatch acknowledgment rather than whether a workflow is paused/running/completed. Clients use the existing status route for authoritative runtime state. Discovery can work with a worker stopped or drafts unavailable, but requires configured session providers and trusted placement through the existing backend factory. Listing does not attest archived plan validity; full detail paths retain integrity checks.

No raw values, plans, live runtime state or other owners' sessions are returned. The optional local launcher gains discovery automatically when debugging is enabled. Browser lists, search/filter UX, cancellation, expiry, event replay and production-provider acceptance remain separate work.
