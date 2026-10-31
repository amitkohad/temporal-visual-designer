# Discover saved Dev HTTP mock fixtures

Explicitly enabled hosts expose signed `GET /mock-fixtures` under
`/api/v1/organizations/{organization_id}/environments/{environment_id}`. It uses
the [fixture administration](http-mock-admin.md) provider and policy: fresh
administrator permission plus explicit Dev enablement. UAT and Prod are denied.

The response contains `items` with scope, connection ID/revision, fixture digest
and encoded size, plus `next_after_connection_id` and
`next_after_connection_revision`. No fixture path, request/response body or headers
are returned. Default page size is 25; `limit` accepts 1 through 50.

To continue, pass both cursor fields from the preceding response as query
parameters. A null pair means there is no further page in that query's view.
Both identifiers are required together; unknown or duplicate query parameters
are rejected. Ordering is lexical by connection ID, then revision, so `v10`
sorts before `v2`. The cursor is exclusive and scoped by the authenticated route.

Pagination is a current view rather than an export snapshot. Concurrent new keys
after the cursor may appear; keys inserted at or before it are not backfilled.
Previously emitted immutable keys do not repeat. Restarting the host preserves
keys and allows continuation from the same cursor.

Listing validates stored metadata and document type/size without loading fixture
JSON into the application. It does not recompute the content digest. Fetch the
exact revision's verified metadata through the existing GET before use; actual
execution independently validates the pinned fixture again. Same-sized content
corruption can appear in listing and will still fail those later checks.

The host must provide the listing port; old custom write/read-only providers
return unavailable for listing. Defaults, browser screens, body download,
history/delete and package transfer remain separate work. See
[ADR 0058](decisions/0058-http-mock-listing.md).
