# Use mock fixture administration in the local demo

Initialize a local demo, then explicitly enable fixture administration before
serving it:

```sh
uv run nexusflow-local init --state-dir .nexusflow-local
uv run nexusflow-local enable-mock-fixtures --state-dir .nexusflow-local
uv run nexusflow-local serve --state-dir .nexusflow-local
```

The enable command creates `http-mock-fixtures.json` and the private
`http-mock-fixtures.db` store. It adds the synthetic demo user's
`organization_administrator` role in Dev and prints that role in its result.
That existing role includes membership/connection administration and task
reassignment; it is broader than fixture administration alone. UAT/Prod and
worker permissions are not granted. Keep this installation synthetic and local.

Use the existing `token` command to authenticate the
[JSON fixture APIs](http-mock-admin.md) and
[paginated discovery API](http-mock-listing.md). New signed requests enforce the
current stored grant, including revocation. No fixture content or token is
printed by enablement. Store only synthetic request/response data.

The APIs preserve immutable scoped revisions and digest pins across a full host
restart. Re-running enablement for a valid profile is idempotent; it does not
restore revoked grants, membership or original author permissions. Stop the
running host before enablement; the existing installation lock excludes concurrent
commands that modify state.

Missing/corrupt/mismatched marker or database state causes startup to fail without
repair. Preserve the state for diagnosis rather than deleting files to bypass
validation. The marker is bound to one installation and its fixed Dev scope.
Read-only schema validation includes committed WAL changes. SQLite may manage
transient locking sidecars; the loader does not recreate fixture tables or rows.

The profile enables fixture administration APIs only. It does not publish HTTP
activities, register a mock workflow dispatcher or enable live HTTP. Existing
welcome workflow execution remains available under its original author grant.
Browser screens, fixture packages and production deployment remain open. See
[ADR 0059](decisions/0059-local-mock-fixtures.md).
