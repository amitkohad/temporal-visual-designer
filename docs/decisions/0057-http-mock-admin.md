# ADR 0057: Explicit Dev HTTP JSON fixture administration

Status: accepted for explicitly enabled local administration APIs.

## Decision

Expose immutable HTTP JSON mock fixture writes and metadata-only reads using the
existing signed, organization/environment-scoped HTTP API. Both operations require
fresh `connection.manage` permission and a Dev scope explicitly enabled by the
host. A route named `dev` alone does not enable the feature. UAT and Prod are
denied even if supplied in an invalid host profile. Default hosts have no fixture
provider or enabled scopes; this increment adds no launcher registration.

Freeze `HTTPMockFixtureWrite`, `HTTPMockFixtureMetadata`, storage and private
administration errors before parallel service/HTTP/acceptance work. Routes select
scope, connection ID and revision. Payloads cannot override these fields or the
fixture digest. JSON object request/response bodies use the existing bounded
canonical encoding. GET/HEAD request bodies and HEAD/204/304 response bodies are
forbidden. Binary bodies, response headers and scripted fixtures remain outside
this API subset; the internal storage port retains its wider private format.

PUT creates one immutable fixture for the route key; identical retries return
equivalent metadata and changed content conflicts. GET returns scope, key, digest
and encoded size only. It internally loads and validates a private scenario to
verify content integrity; it is not a metadata-only database query. Neither route
returns request paths, request/response bodies or headers. Existing boundary
handling supplies no-store responses and fixed payload-free errors.

The service authorizes before provider work and rechecks permission/Dev policy
after a provider call before releasing metadata, including after asynchronous
lookup. A write may have committed if permission is revoked during storage work;
an authorized exact retry recovers its immutable result. Validate provider result
types, scope/key bindings and receipt digest/size against the canonical fixture.
Normalize unexpected private provider failures; preserve signed identity failure
classification. The provider lifecycle remains owned by the host.

## Trust and limitations

The feature administers synthetic Dev fixture data. It does not resolve secrets,
classify environments from Entra, validate a live connection, grant worker
permission or change execution mode. Actual mock execution still checks trusted
Dev eligibility and fresh worker permission before and after fixture lookup.
No network, live transport fallback, runtime start or publication occurs on PUT
or GET. A fixture can be prepared before a corresponding live connection exists.

Private SQLite fixtures remain plaintext host data. No external change request,
production deployment or cloud persistence capability is added. Business-user
Designer forms, Admin browser screens, list/history/delete, fixture package
transfer, default launcher wiring and persistent-fixture Temporal restart
acceptance remain open.

## Verification and ownership

The service lane owns validation/authorization/provider mapping; the HTTP lane
owns optional backend composition, routes and signed endpoint tests; independent
acceptance composes signed HTTP writes, reopened SQLite storage and actual mock
execution. The coordinator owns shared contracts, docs, checkboxes and regression.
See [increment evidence](../../specs/001-nexus-flow-designer/implementation-http-mock-admin.md).
