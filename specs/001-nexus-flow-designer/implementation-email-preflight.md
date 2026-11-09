# Email preflight increment

Branch: `codex/065-email-preflight`, based on main `10fc7bb` (PR #62).

## Frozen interfaces and parallel plan

`email_preflight_types.PreparedEmailEnvelope` is a frozen independent snapshot:
scope, operation_id, connection_id/revision, definition_digest, execution_mode
live, smtp_host, port, sender, to/cc/bcc tuples, subject, text_body and optional
secret_reference. SMTP destination, sender, recipients, content and reference are
private in repr. The plan is configuration data, not an egress authorization or
reusable sending permit.

`EmailPreflightError` has fixed text `Email preflight failed` and closed codes
invalid_request/forbidden/not_found/blocked_recipient/unavailable/timeout/
invalid_provider_result. retryable and delivery_may_have_occurred are False.

`email_envelope.prepare_email_envelope(scope,operation_id,record,inputs)` validates
strict trusted context, exact EmailConnectionRecord/metadata keys/digest/secret
flag/UTC format, bounded duplicate-free canonical definition and input snapshots.
It selects sender/SMTP/reference from the pinned definition, never business input.
Every canonical To/Cc/Bcc domain must match allowed_recipient_domains exactly;
no wildcard/subdomain inference. Only live mode is supported by this preflight;
mock authoring metadata does not provide a mock executor. No DNS, SMTP or secret
lookup occurs. Invalid business context is invalid_request, mock mode forbidden,
malformed provider record invalid_provider_result, domain denial blocked_recipient.

`email_preflight.EmailPreflightPlanner(repository,authorization,*,timeout_seconds=5)`
provides async prepare(scope,operation_id,inputs)->PreparedEmailEnvelope. Require
fresh worker.execute for the exact scope/connection before any private read and
again after successful lookup, then apply the pure envelope policy. Run the
synchronous repository read off the event loop. Bound total await with a native
finite0<timeout<=30 deadline and late-answer/cancellation guards. Preserve delivered
caller cancellation. Storage not_found maps to not_found; unexpected/private
provider errors normalize to fixed unavailable; permission failures forbidden.
Scope and operation ID are host inputs, never selected by message JSON.

1. [P] Envelope lane owns frozen types, pure record/message policy and tests.
2. [P] Planner lane owns fresh policy/read/deadline orchestration and tests.
3. [P] Independent acceptance lane composes signed registry, actual SQLite and
   prepared messages with separate synthetic worker grants and network forbidden.
4. Coordinator owns docs, verified checkboxes and regression evidence.

## Verification

Focused verification passed: **74 pure envelope tests** (0.70 seconds), **58 planner
tests** (1.04 seconds) and **18 independent acceptance tests** (19.70 seconds).
The 150 cases cover native context/model validation, canonical pinned records,
UTC/digest/key/secret flags, exact To/Cc/Bcc policy, independent private snapshots,
fresh resource-bound worker authorization and fixed errors with no delivery.

Planner tests verify off-loop reads, caller mutation isolation, malformed/sync-port
coroutine handling, real phase-armed deadlines, late/suppressed cancellation and
actual thread completion after a cancelled await without result release.
Independent acceptance composes signed registry administration, reopened SQLite
and separate synthetic worker grants. It proves administrator rights do not grant
preflight access, later revisions do not alter earlier pins, revocation after a
private read blocks release, and corrupted/foreign records fail privately.
DNS/socket/SMTP/secret/runtime sentinels are untouched; no live identity/provider
or delivery acceptance is inferred.

Independent review found no actionable blockers. Ruff lint/format, configured
mypy (138 source files), lock verification and source/wheel build passed. Wheel
parity passed for all three final preflight modules. Full default regression
passed **4,209 tests**, with eight existing platform/permission skips and 91
integration tests deselected (791.31 seconds). Final independent review and diff
checks passed. No dependencies, public API, worker activity or default provider
registrations changed. No transport or new Temporal acceptance is claimed.

## Remaining scope

Prepared envelopes are not sent. DNS/TLS pinning and egress restrictions, secret
resolution, SMTP/API transport, mocks, durable delivery ambiguity/retry recovery,
worker registration, executable publication, browser UI and deployed identity
remain open. Cancellation can stop awaiting storage without forcibly stopping an
already running SQLite thread; no external delivery effect is introduced.
