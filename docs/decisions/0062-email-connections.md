# ADR 0062: Immutable email connection metadata

Status: accepted for explicit metadata administration; no email transport.

## Decision

Add an email-specific connection registry and signed administrator routes alongside
the existing HTTP registry. Keep provider contracts separate so HTTP callers and
stored definitions retain their current shape. Hosts explicitly inject
`ApiBackend.email_connections`; default applications and local grants/providers
are unchanged.

The strict private definition selects `smtp_tls`, a canonical qualified ASCII
SMTP host, integer port (default 465), canonical sender mailbox, exact allowed
recipient domains and optional opaque secret reference. It contains no password,
token, arbitrary headers or executable code. Host/domain normalization performs
no DNS, certificate, connection or credential lookup. A valid definition is not
proof that a destination is reachable or authorized for future execution.

Recipient domain policy is an explicit nonempty sorted unique set, capped at 50.
There are no wildcard, suffix or implicit subdomain grants. Sender policy and
recipient policy are independent: an organization may send to approved partner
domains. Runtime enforcement against message recipients is separate work; this
increment stores policy rather than sending mail.

## Persistence and administration

Use immutable organization/environment/connection/revision keys with original
metadata retained on exact canonical retries. Changed same-key definitions
conflict. Serialize sequence/quota claims in immediate transactions across
independent repository instances. Default quotas follow the HTTP registry:
100 connections per scope and 100 revisions per connection.

Public metadata includes identity, numeric revision, connector/version/transport,
definition digest, presence of a secret reference, actor and UTC creation time.
It excludes SMTP host/port, sender, recipient-domain policy, reference value and
private definition JSON. Metadata/history reads never load private definitions;
private host reads verify canonical definition, digest and key bindings. Bound
and validate stored metadata/checksums before public serialization. Integrity
checks detect corruption, not a malicious database administrator with file access.

All routes require fresh scoped `connection.manage`. Check authorization before
provider discovery/work and validate provider metadata before releasing a result.
Use fixed private errors, no-store responses, strict route IDs and bounded history
pagination. Organization administrators may manage their authorized Dev/UAT/Prod
definitions; production support alone does not grant connection administration.
No permission or provider is installed by exposing the optional routes.

## Limits and evidence

No email is sent and no credentials are resolved. SMTP/API transport, trusted
worker permission, sender/recipient enforcement, DNS/TLS egress controls, actual
mocks, delivery ambiguity/retry recovery, worker dispatch, executable publication
and browser UI remain open. The SQLite file is host-private metadata storage with
opaque secret references, not a secret vault or production identity integration.

See [increment evidence](../../specs/001-nexus-flow-designer/implementation-email-connections.md).
