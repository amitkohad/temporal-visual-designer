# ADR 0052: Fresh-authorized credential-free HTTP coordination

Status: accepted for the internal, non-durable single-attempt subset of feature 08.

## Decision

Compose the scoped registry, destination planner and pinned sender through
`HTTPAttemptExecutor.execute(scope, destination_request, attempt_request)`.
The caller supplies a scoped connection revision, operation ID and relative path,
not a pre-approved routing record. Freeze `CredentialFreeDestinationPlanner`,
`HTTPExecutionError` and the optional synchronous sender `before_write` hook
before parallel implementation. Share strict method/body validation and snapshot
creation through the pure `checked_attempt` helper.

The executor is an explicitly composed internal library. It is not registered as
a Temporal activity or exposed through the API/local launcher. Operation IDs are
caller-supplied internal identifiers; this does not establish durable run/step
binding, deduplication, an effect journal or exactly-once execution.

## Authorization and credential gates

`prepare_credential_free` authorizes and integrity-checks the immutable scoped
definition, then rejects every configured secret reference with
`credentials_required` before DNS. Credential-bearing definitions are never
silently downgraded to anonymous calls, and no secret provider is invoked.

After DNS, the executor calls synchronous `revalidate_credential_free` to recheck
permission, definition integrity, absence of credentials and the exact initial
scope/revision/digest/operation/path/origin/host/port/public-IP binding. Redirect
plans are not accepted as initial attempts. This gate precedes sender entry.

The actual sender invokes the execution guard again after verified TLS, just
before its first HTTP write. Denial closes the connection without transmitting
the HTTP request. The guard repeats authorization and definition checks and must
finish within the executor deadline. Its synchronous contract avoids an extra
async gap between this decision and writing. These are fresh checks at named
boundaries, not atomic revocation across an external network effect; a permission
change after the final decision cannot recall bytes already being sent.

Sender and planner ports are trusted host adapters. The executor validates
provider results and rejects missing, repeated or failed guards; it cannot undo
side effects from a malicious adapter that disregards its contract. A routing
dataclass is still not an unforgeable authorization token. Standalone low-level
transport remains available for trusted callers under its existing contract.

## Bounds, privacy and failure semantics

Snapshot strict input values before provider access. One outer deadline covers
preflight, revalidation and the single send, with monotonic late-result and
cancellation-count checks. Preserve the sender's bounded cleanup behavior.
Synchronous trusted providers must be bounded/cooperative; timeout cannot preempt
arbitrary synchronous blocking code or stop OS DNS work already in an executor.

Validate result identity, status, header shape/size and body bytes before
returning a private result. Raw headers/body/query data remain unsuitable for
logging, debugger output or package export. This is not response redaction or
workflow result mapping. Errors use closed codes and fixed text; `retryable` is
always false.

Before sender entry, failures have no transmission. After entry, unknown errors,
outer deadlines and invalid provider results conservatively indicate possible
delivery. Preserve valid, trusted transport flags for known failures such as TLS
or guard denial before writing. Cancellation propagates, with delivery unknown
if it interrupted a send. No automatic retry, alternate address, proxy or
redirect chain occurs. Return 3xx responses privately; redirect method/body and
sensitive-data forwarding rules remain a separate decision.

## Parallel ownership and verification

Coordinator: shared contracts, request policy, sender guard, docs/checklists and
regression. Planner lane: credential-free preparation/revalidation and tests.
Executor lane: coordination/results/failure semantics and isolated tests.
Acceptance lane: independent real SQLite/planner/transport/TLS composition with
fake DNS and a test-only asserted numeric-pin dial redirect to loopback.

Controlled TLS acceptance does not establish live public DNS/egress, Entra,
deployed isolation, workflow dispatch or browser behavior. Credentials, durable
effect/retry policy, run/project/step binding, redirect forwarding, descriptors,
mocks, other connector types and workflow/Designer integration remain open.
