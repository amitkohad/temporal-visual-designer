# Email connection metadata increment

Branch: `codex/064-email-connections`, based on main `fcfe55e` (PR #61).

## Frozen interfaces and parallel plan

`email_connection_types.EmailConnectionWrite` is a strict Contract with
connector_type email/version1.0.0, transport smtp_tls, smtp_host, port (native
integer1..65535, default465), sender, allowed_recipient_domains and optional
secret_reference Identifier. DNS-style hosts/domains are bounded ASCII qualified
names with labels1..63, total<=253, no IP literals/URLs/ports/trailing dots or
wildcards; domains lowercase. Sender uses the existing bounded mailbox subset.
Allowed recipient domains are an immutable sorted unique tuple (JSON arrays
accepted), 1..50 exact domains with no subdomain/wildcard inference. This stores
policy; it does not authorize egress or implement delivery.

Public `EmailConnectionMetadata` has the HTTP metadata shape with connector_type
email, version1.0.0 and transport smtp_tls. Private `EmailConnectionRecord` holds
metadata and definition_json. `EmailConnectionPage` has bounded tuple items and
next_before_revision. `EmailConnectionRepository` is a runtime-checkable port with
put(scope,id,revision,request,actor_id), get(scope,id,revision), metadata(...),
and list(scope,id,limit=25,before_revision=None), matching HTTP conventions.
Use existing fixed/sanitized ConnectionError semantics.

`SQLiteEmailConnectionRepository(database,maximum_connections_per_scope=100,
maximum_revisions_per_connection=100)` owns the email_connections table. Retain
immutable canonical definitions, original metadata on exact retries, atomic
per-scope/per-connection quotas and checksummed bounded metadata integrity.
Metadata/list queries must never load private definitions. Private reads validate
definition/digest/key binding. Transactions and recovery follow the HTTP registry.

`EmailConnectionService(repository,authorization)` provides put/get/list using
fresh connection.manage before provider access and validates private provider
results; public get uses metadata only. `ApiBackend.email_connections` is optional
None by default; email_connection_service authorizes before provider lookup.
Signed PUT201/GET200 `/email-connections/{id}/revisions/{revision}` and GET200
`/email-connections/{id}/revisions` mirror HTTP query/pagination/privacy/error
behavior. Organization/environment route scope is authoritative. Default launcher
grants/providers remain unchanged and no SMTP/secret provider is invoked.

1. [P] Storage lane owns typed definitions, SQLite implementation and unit tests.
2. [P] Service/HTTP lane owns fresh administration, optional composition and tests.
3. [P] Independent acceptance lane owns actual signed SQLite restart, privacy,
   scope/role/revocation/integrity and no-network cases.
4. Coordinator owns docs, verified checkboxes, review and regression evidence.

## Verification

Focused verification passed: **105 definition/storage tests** (1.64 seconds),
**60 service and 27 signed API tests** (21.91 seconds combined), and **13 independent
acceptance tests** (17.86 seconds). The 205 cases cover canonical private settings,
strict port/sender/domain boundaries, atomic quota/retry races, scoped history,
bounded SQL corruption guards, no private-column reads for public metadata,
provider result/error validation and pre/post fresh authorization.

Independent acceptance composes actual signed local APIs and SQLite across reopen,
checks original retry metadata, corruption/privacy distinctions, Dev/UAT/Prod
administrator versus support roles and revoked access before storage. Existing
typed email inputs can reference returned IDs; that is not execution readiness.
DNS/socket/SMTP/secret/runtime sentinels remain untouched. Synthetic identities
do not establish deployed Entra/provider/delivery acceptance.

Independent read-only review found no actionable blockers. Ruff lint/format,
configured mypy (135 source files), lock verification and source/wheel build passed.
Wheel parity passed for all five final connection/service/API modules.

The initial full run passed 4,058 cases and exposed an existing 10-millisecond
mock deadline test whose setup could expire before its fixture provider entered.
The test now arms the real timeout after provider entry, preserving cancellation,
cleanup, private errors and no-delivery assertions. Its full file passed 57 cases
(0.66 seconds), and independent review passed. No production timeout behavior
changed. Final full default regression passed **4,059 tests**, with eight existing
platform/permission skips and 91 integration tests deselected (637.96 seconds).
Final diff checks passed. No dependencies, default providers/grants or runtime
registrations changed. No transport or new Temporal acceptance is claimed.

## Remaining scope

No email is sent. Runtime sender/recipient checks, DNS/TLS egress, secret resolution,
SMTP/API transport, mocks, durable delivery/retry recovery, worker dispatch,
executable publication, browser forms and production identity remain open.
