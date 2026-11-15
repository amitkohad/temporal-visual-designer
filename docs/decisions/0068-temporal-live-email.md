# ADR 0068: Opt-in Temporal live email SMTP dispatch

Status: accepted for explicit host/worker registration of live email text proof
dispatch; default launcher remains off until `enable-live-email`.

## Decision

Provide `EmailTextActivities` as an explicit Temporal bridge that can own
`nexusflow.proof.dispatch` for the exact email text descriptor reference
(`nexus.email.text@1.0.0`). Live forbids `fixture_digest` and mock mode; email
mocks remain unimplemented. Missing live ports fail without fallback.

Derive a run-bound operation identity with kind
`temporal-email-live-operation`. Identity stays stable across retry attempts and
differs for a new run. Live remains `maximum_attempts=1` / non-retryable at the
descriptor and application-error boundary; durable ambiguous effect recovery
stays open. Activity output is status-only (`operation_id`, `execution_mode`,
`submission_status`) — never credentials, MIME bytes, recipients, or subject.

When both live HTTP and live email profiles are enabled, the host registers a
single proof-dispatch owner that routes by `ActivityCall.activity_id` to the
HTTP JSON or email text bridge. Two Temporal activities must not share the same
name. Mock-only HTTP factories remain unchanged.

## Local-demo enablement

`nexusflow-local enable-live-email` writes a Dev-only profile marker and ensures
a local SMTP secret map file exists. `put-smtp-secret` stores login credentials
keyed by opaque reference. `serve` loads the profile, opens
`email-connections.db`, and registers email proof dispatch on the owned Temporal
worker with the authorized send executor. Without the profile, email proof
dispatch stays unregistered.

Worker authorization for live email is a dedicated Dev grant of `worker.execute`
for connection resources (`local-live-email-worker`). Revoked grants are never
restored by enable retries. Connection administration continues to store only
the reference label.

Local-demo always opens `email-connections.db` for signed metadata APIs when the
installation is initialized (same lifecycle pattern as HTTP `connections.db`).
That wiring does not enable send or register workers by itself.

## Non-claims

Direct compiled-plan Temporal acceptance does not establish built-in project
publication, palette registration, Entra/Key Vault, public SMTP egress, browser
F08-A01–A05, or F04-T007 custom Python sandbox isolation. local-demo secrets and
loopback SMTPS are not production-provider acceptance. Preflight and MIME do not
send mail.
