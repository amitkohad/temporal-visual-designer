# Deterministic email MIME encoding increment

Branch: `codex/066-email-mime`, based on main `031b435` (PR #63).

## Frozen interfaces and parallel plan

`email_mime_types` provides fixed private `EmailEncodingError` with closed codes
invalid_request/message_too_large/unavailable, retryable False and
delivery_may_have_occurred False. Frozen `PreparedEmailMime` contains envelope
(repr private), message_time and message_id. Frozen `EncodedEmailMessage` contains
scope, operation_id, connection_id/revision, definition_digest, message_id,
message_time, content_digest, envelope_sender (private), recipients tuple
(To+Cc+Bcc, private) and data bytes (private).

`email_mime_policy.prepare_email_mime(envelope,message_time)` strictly validates an
exact PreparedEmailEnvelope, its context, live mode, canonical SMTP/sender/message
fields and private opaque reference. Rebuild independent snapshots; this does not
recheck permission, record digest against storage or allowed recipient domains.
Time must be native datetime, timezone.utc, zero microseconds, year>=1900. Sender
and To/Cc/Bcc are canonical existing mailbox subsets. A snapshot is not authority.

Message-ID is `<nexusflow-{sha256}@nexusflow.invalid>`. Hash canonical identity:
kind email-message-id, schema_version0.1, scope org/env, operation_id,
connection_id/revision and definition_digest. No wall clock/random/hostname,
recipient, content, secret-reference or message-time fields enter this identity.
It stays stable for the same logical context; it does not validate payload reuse
or provide provider deduplication. Caller must persist/reuse its message_time for
identical retry bytes; payload/envelope binding belongs to future effect recovery.

`email_mime.encode_email_message(envelope,*,message_time,max_message_bytes=2097152)`
returns EncodedEmailMessage. Native integer size limit1..8388608. Use standard
EmailMessage with explicit SMTP policy (CRLF, utf8 False, max_line_length78,
7bit output), headerregistry Address values, explicit From/To/optional Cc/Subject/
Date/Message-ID, text/plain UTF-8 base64 content. Never add Bcc headers, SMTP host,
secret references or arbitrary client headers. SMTP envelope recipients include
all To/Cc/Bcc in that order. Body newline normalization CRLF/CR->LF with one final
LF if absent precedes SMTP text serialization, which canonicalizes decoded body
newlines to CRLF; no content redaction is performed.
Validate bounded complete ASCII wire data, CRLF only, terminal CRLF and line
length<=998. Size failure message_too_large; invalid input/time/limit invalid_request;
unexpected serializer failure unavailable. content_digest is SHA256 of data bytes.
These are message bytes only, with no SMTP commands/terminator or transport call.

Subject encoding uses generated RFC 2047 base64 words with at most 39 UTF-8 bytes
per codepoint-safe chunk and CRLF+space folds retained without refolding. This
preserves exact mixed Unicode/ASCII whitespace and valid Unicode line separators
that stdlib helpers can change. No raw client headers are accepted.

1. [P] Policy lane owns private types, strict input/time/identity and tests.
2. [P] Codec lane owns deterministic bounded MIME bytes and parser/size tests.
3. [P] Acceptance lane independently composes signed registry, preflight and
   codec, checks Bcc/envelope/privacy and standard parser semantics without network.
4. Coordinator owns docs, verified checkboxes and final regressions.

## Verification

Focused verification passes: **59 policy tests**, **43 codec tests** and
**13 independent acceptance tests**. These 115 cases cover strict native context,
canonical mailbox/message/reference snapshots, UTC/time/identity partitioning,
private frozen results, exact Unicode subjects, decoded CRLF body normalization,
recipient ordering, Bcc header omission, inclusive wire-size limits, malformed
serializer output and fixed private errors. No external provider effect occurs.

Independent acceptance composes signed administrator APIs, real SQLite and
worker-authorized preflight. Later immutable revisions do not change earlier
pins; direct encoding does not renew worker authority. Independent cross-review
caught and fixed stdlib subject-folding whitespace/Unicode separator changes.
Additional deterministic review probes passed 1,000 generated subject and 300
mailbox round trips with no parser defects. Protocol review bounds encoded-word
headers to 76 columns, including the initial Subject prefix.

Ruff lint/format, mypy (141 source files), lock check, source/wheel builds and
normalized source-to-package parity for all three new modules pass. No dependency
or public API/schema change was needed. Full default regression passed:
**4,324 passed, 8 existing host skips, 91 integration cases deselected** in
527.29 seconds. Seven skips require unprivileged symlink support; one is a
Linux-specific TCP TIME_WAIT regression. Bounded F08-T01p/T02p/T04p/T05p/T06p and
NF-T012p are confirmed; their parent connector/UI/delivery tasks remain open.
Temporal integration was not rerun for this offline encoder; it adds no activity
registration or workflow execution.

## Remaining scope

No email is sent. Permission/recipient/egress checks must be fresh at future send
boundaries. DNS/TLS controls, secret resolution, SMTP/API transport, mocks,
durable effects, worker registration, executable publication and UI remain open.
Message-ID is not exactly-once delivery or a signature over message content.
