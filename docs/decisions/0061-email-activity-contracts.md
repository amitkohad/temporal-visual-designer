# ADR 0061: Email text activity authoring metadata

Status: accepted for offline authoring; no email execution capability.

## Decision

Introduce a distinct `nexus.email.text@1.0.0` activity contract and descriptor.
Business inputs select an email connection/revision, explicit live/mock mode,
recipients, subject and plain text body. Mock mode requires a digest pin; live
mode forbids one. These are authoring fields for future providers, not evidence
that an email connection, mock fixture or transport exists.

Keep sender identity, credentials, transport destination, scope, operation ID and
arbitrary headers outside the business input. Support bounded ASCII mailbox and
DNS-style domain syntax as an explicit initial subset, rather than claiming full
email address syntax. Reject display names, comments, quoted local parts,
Unicode mailbox syntax, address literals and whitespace/control injection.
Canonicalize domain case, preserve local spelling and reject exact canonical
recipient duplicates across To/Cc/Bcc. Require at least one To recipient and cap
the total at 50.

Subject must be nonblank, at most 256 characters/1,024 UTF-8 bytes, with no C0/C1
controls. Plain text body is nonempty and at most 1 MiB of UTF-8, permits ordinary
newlines and rejects NUL. Attachments and HTML are outside this first contract.
These are authoring limits; no transport encoding or delivery behavior is tested.

Typed input models are revalidated at the preparation boundary, including
constructed or mutated objects. Return an independent frozen message snapshot
with tuple recipient fields and private recipient/subject/body representation.
Validation errors from that boundary contain fixed text and a closed code, not
message content. Models and exported schemas cannot grant permission or bypass
future runtime connection/sender/recipient checks.

## Metadata and compatibility

Content-address the canonical versioned descriptor metadata; do not call it an
executable artifact. Declare the unregistered `connector.email.text.v1` capability
and an email connection requirement. Use reference-free portable schemas for the
existing offline authoring validator. Backend byte, canonicalization and aggregate
rules remain authoritative where the portable schema cannot express them.

Descriptor-only exact lookup is opt-in and separate from the executable Python
catalog. The default catalog, API palette, worker and launcher remain unchanged.
Existing publication must reject this metadata as an executable dependency. HTTP
and email descriptors remain independently selectable in offline authoring.

The output contract contains operation identity, selected mode and submission
status `accepted`. This describes a future provider submission receipt, not
recipient delivery. The declared one-attempt policy does not supply an email
retry/ambiguity strategy; durable effects and provider idempotency require later
design and implementation.

## Remaining scope

No email is sent. Email connection administration, secret resolution, sender and
recipient destination policy, SMTP/API transport, actual email mocks, durable
delivery recovery, worker dispatch, executable publication and browser forms
remain open. Independent acceptance is offline validation with network forbidden,
not provider or production acceptance.

See [increment evidence](../../specs/001-nexus-flow-designer/implementation-email-activity-contracts.md).
