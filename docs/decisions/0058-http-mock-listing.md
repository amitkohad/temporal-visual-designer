# ADR 0058: Bounded Dev fixture metadata discovery

Status: accepted for explicitly enabled local administration APIs.

## Decision

Add scoped fixture metadata enumeration to the existing private SQLite adapter
and Dev administrator service. Expose signed `GET /mock-fixtures` under the
organization/environment API prefix. Reuse the fresh `connection.manage` and
explicit host-enabled Dev policy; this increment adds no permission, default
provider or production capability.

Freeze private `HTTPMockFixtureReceiptPage`/listing port and public
`HTTPMockFixturePage` before parallel repository, service/HTTP and independent
acceptance work. Default page size is 25, maximum 50. Require either no cursor or
both `after_connection_id` and `after_connection_revision`. Use an exclusive
lexical pair ordered by the existing ASCII identifiers, not semantic version or
creation order. Read at most `limit + 1` metadata records to determine a next
cursor; return the last emitted key only when another record exists.

Each query returns a coherent database statement view. Pagination across requests
is not a frozen snapshot: concurrent inserts after the cursor may appear, and
inserts at or before it are not backfilled. Immutable keys and exclusive cursors
prevent already-emitted keys from repeating. A cursor never grants access to
another scope. No offset, total count or unbounded scan API is provided.

## Metadata and trust boundaries

Enumerate only route identity, fixture digest and encoded byte count. Bound stored
key/digest types and lengths before materializing them into Python. Check stored
document type/byte length and metadata format/bindings, including the lookahead
row. Never fetch or decode private fixture JSON for listing. SQL length/type
expressions may inspect storage internally; this is not a promise of zero disk
access to private columns.

A listed digest is stored discovery metadata, not content verification, execution
readiness or a signed integrity guarantee. Same-sized content corruption can
remain visible in discovery. Existing exact GET and executor lookup still load
and validate the canonical document, scope/key and digest and fail closed on
corruption. Clients must not treat listing as a replacement for these checks.

The service validates provider page types, scope, ordering, bounds and cursor
consistency, returns independent metadata snapshots and reauthorizes before
releasing a successful page. Unsupported listing providers report private
unavailable errors after permission checks. Reject unknown/duplicate query fields,
unpaired cursors and invalid limits with the existing payload-free API errors.

## Remaining scope and evidence

This supports future Designer discovery; it adds no browser screens, fixture body
download, history/delete, project packaging or default launcher wiring. Binary
fixtures already present in the internal store may have discoverable metadata;
JSON-only authoring remains the existing write API subset. No live network,
worker grant, workflow start or production identity claim is added.

See [increment evidence](../../specs/001-nexus-flow-designer/implementation-http-mock-listing.md).
