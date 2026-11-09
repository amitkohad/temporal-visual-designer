# ADR 0063: Worker-authorized email envelope preflight

Status: accepted for configuration preparation; no email delivery capability.

## Decision

Compose existing email messages and immutable connection definitions into a
private prepared envelope before transport is implemented. The caller supplies
trusted scope and operation identity. Business JSON cannot choose sender,
SMTP destination, secret reference or host context.

Require fresh `worker.execute` for the exact scope/connection before private
definition lookup and after successful lookup. Administrator permission to store
a definition is separate. Validate returned record type, scope/key, connector and
transport versions, canonical UTC metadata, definition digest and secret-reference
presence. Parse only bounded, duplicate-free canonical private definitions.
Select sender/destination/reference from the pinned immutable definition.

Every canonical To/Cc/Bcc recipient domain must match the stored exact allowlist.
Do not infer wildcard, suffix or subdomain permission. Revalidate message models,
including constructed/mutated objects, and return independent frozen tuple/string
snapshots whose representation hides all private envelope data.

This planner accepts live-mode authoring only. Reject mock mode explicitly until
an email mock provider exists. No DNS, TLS, SMTP, secret resolution, runtime start
or external delivery happens during preflight.

## Deadlines and errors

Run synchronous repository reads off the event loop and bound the whole operation
with a finite deadline. Check elapsed/cancellation state before returning, including
after synchronous policy work and late provider results. Propagate delivered
caller cancellation. Cancelling the await does not forcibly stop an already
running SQLite thread; no external delivery effect exists to recover here.

Use fixed private `EmailPreflightError` messages and closed codes for invalid
requests, permission, missing definitions, blocked recipients, timeout, malformed
provider records and unavailable ports. Report no automatic retry or possible
delivery. Never copy raw provider diagnostics or message data into errors.

## Limits and evidence

The result is prepared configuration, not egress approval or a reusable sending
permit. Future execution still needs fresh authorization at its side-effect
boundaries, destination/DNS/TLS policy and credential resolution. Snapshot
preparation does not promise delivery or exactly-once effects.

Independent acceptance uses signed connection administration, actual SQLite and
separate synthetic worker grants with network/secret/runtime sentinels forbidden.
It does not establish live Entra, SMTP/API transport or a registered Temporal
activity. Email mocks, durable delivery ambiguity/retries, executable publication,
default launcher registration and browser UI remain open.

See [increment evidence](../../specs/001-nexus-flow-designer/implementation-email-preflight.md).
